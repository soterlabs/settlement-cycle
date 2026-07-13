"""HyperSync-direct ``IDebtSource`` — raw-log query, no HyperIndex.

Why not HyperIndex? The MakerDAO Vat records frob/grab via the ``note`` modifier,
which emits an **anonymous** ``LogNote`` with 4 indexed topics. HyperIndex's event
decoder can't handle anonymous events with 4 indexed params — it reserves topic0
for the event-signature hash, so 4 indexed → 5 topics → decoder build fails
(``topic_count must be 1..=4``). This is a known, unimplemented feature request:
https://github.com/enviodev/hyperindex/issues/990

HyperSync's low-level query API, however, filters logs by **raw topic0**, so we
match the frob/grab selector directly. Transport + pagination + persistence are
the shared ``extract.hypersync`` / ``extract.hypersync_store`` layers (the same
reorg-safe path the balance source uses — logs are fetched once and served from
Postgres on re-runs); this module only decodes darts and aggregates. The output
contract matches ``DuneDebtSource`` — normalised Art (Σ dart, wad), NOT
rate-scaled; the Vat.rate index is applied downstream in ``normalize/debt.py``.

Dune parity: the SQL enforces ``block_date >= start_date`` per call — the same
per-call ``start`` filter is applied here (never a process-wide env knob, which
would silently under/over-count any second prime sharing the process).

Config (env):
    ENVIO_API_TOKEN   required — free token from https://app.envio.dev/api-tokens
    HYPERSYNC_URL     optional — endpoint override (see ``extract.hypersync``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from typing import Any

import pandas as pd
import requests

from ...extract import hypersync_store
from ...extract.hypersync import HyperSyncError

__all__ = ["HyperSyncDebtSource", "HyperSyncError", "_decode_dart"]

_WAD = Decimal(10) ** 18
_VAT = "0x35d1b3f3d7966a1dfe207aa4514c12a259a0492b"
# Anonymous LogNote topic0 = 4-byte fn selector, left-aligned in the 32-byte word.
_FROB_T0 = "0x7608870300000000000000000000000000000000000000000000000000000000"
_GRAB_T0 = "0x7bab3f4000000000000000000000000000000000000000000000000000000000"
# dart (int256) sits at calldata byte 164; in the raw log `data` the note's
# `bytes` payload is preceded by two ABI words (offset + length = 64 bytes),
# so within raw data dart starts at byte 164 + 64 = 228.
_DART_PAYLOAD_OFFSET = 164


def _decode_dart(data_hex: str) -> int:
    """Decode the signed int256 ``dart`` (raw wad) from a raw LogNote ``data``.

    ``data`` is the ABI encoding of the note's ``bytes`` payload:
    ``[offset word][length word][payload...]`` — so skip the two 32-byte words,
    then read the dart int256 at payload byte 164. Two's-complement. Returned as
    a Python ``int`` (exact) so per-day sums stay exact; the ÷1e18 happens once,
    at the end, under a high-precision Decimal context (matches Dune's DECIMAL).
    """
    raw = data_hex[2:] if data_hex.startswith("0x") else data_hex
    length = int(raw[64:128], 16)              # bytes 32..63 = payload length
    payload = raw[128 : 128 + length * 2]      # payload starts at byte 64
    start = _DART_PAYLOAD_OFFSET * 2
    word = payload[start : start + 64]
    if len(word) != 64:
        raise HyperSyncError(
            f"LogNote payload too short for dart: payload {len(payload)} hex chars, "
            f"need >= {start + 64}"
        )
    v = int(word, 16)
    if v >= 1 << 255:                          # two's-complement sign
        v -= 1 << 256
    return v


class HyperSyncDebtSource:
    """Implements ``IDebtSource`` via the shared HyperSync transport + store.

    Injectable ``post`` for tests: any ``(url, json, headers, timeout) -> resp``
    with ``.ok``/``.status_code``/``.text``/``.json()``. Defaults to
    :func:`requests.post`.
    """

    def __init__(self, post: Callable[..., Any] = requests.post) -> None:
        self._post = post

    def debt_timeseries(self, ilk: bytes, start: date, pin_block: int) -> pd.DataFrame:
        cols = ["block_date", "daily_dart", "cum_debt"]
        # Aggregate in EXACT integer wad using a plain Python dict — a pandas
        # int column would be coerced to int64 whenever every dart fits, and
        # numpy sums/cumsums then WRAP silently past 2^63. Python ints are
        # arbitrary-precision; the ÷1e18 happens once at the end under a wide
        # Decimal context (matches Dune's DECIMAL(38,18) byte-for-byte).
        daily: dict[date, int] = {}
        for row in self._fetch_logs(ilk, pin_block):
            d = datetime.fromtimestamp(int(row["ts"]), tz=timezone.utc).date()
            if d < start:                      # Dune parity: block_date >= start_date
                continue
            daily[d] = daily.get(d, 0) + row["dart"]
        if not daily:
            return pd.DataFrame(columns=cols)
        out: list[dict[str, Any]] = []
        cum = 0
        with localcontext() as ctx:
            ctx.prec = 60
            for d in sorted(daily):
                cum += daily[d]
                out.append({
                    "block_date": d,
                    "daily_dart": Decimal(daily[d]) / _WAD,
                    "cum_debt": Decimal(cum) / _WAD,
                })
        return pd.DataFrame(out)[cols]

    # -- transport (shared reorg-safe store) --------------------------------

    def _fetch_logs(self, ilk: bytes, pin_block: int) -> list[dict[str, Any]]:
        ilk_topic = "0x" + bytes(ilk).hex()
        selections = [
            {
                "address": [_VAT],
                # topics[0] = topic0 (selector) OR-set; topics[1] = ilk.
                "topics": [[_FROB_T0, _GRAB_T0], [ilk_topic]],
            }
        ]
        # Scan floor 0: the Vat predates every allocator ilk and the topic
        # filter keeps the scan cheap; the store persists the finalized
        # history so re-runs fetch only the incremental range. The per-call
        # ``start`` date filter (Dune parity) is applied in debt_timeseries.
        rows = hypersync_store.fetch_logs(
            "ethereum", selections, 0, pin_block, post=self._post,
        )
        return [{"dart": _decode_dart(r.data), "ts": r.block_time} for r in rows]
