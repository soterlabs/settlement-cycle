"""HyperSync-direct ``IDebtSource`` — raw-log query, no HyperIndex.

Why not HyperIndex? The MakerDAO Vat records frob/grab via the ``note`` modifier,
which emits an **anonymous** ``LogNote`` with 4 indexed topics. HyperIndex's event
decoder can't handle anonymous events with 4 indexed params — it reserves topic0
for the event-signature hash, so 4 indexed → 5 topics → decoder build fails
(``topic_count must be 1..=4``). This is a known, unimplemented feature request:
https://github.com/enviodev/hyperindex/issues/990

HyperSync's low-level query API, however, filters logs by **raw topic0**, so we
match the frob/grab selector directly. This source queries HyperSync over HTTP
and returns the same contract as ``DuneDebtSource`` — normalised Art (Σ dart,
wad), NOT rate-scaled; the Vat.rate index is applied downstream in
``normalize/debt.py``.

Config (env):
    ENVIO_API_TOKEN   required — free token from https://app.envio.dev/api-tokens
    HYPERSYNC_URL     optional — default https://eth.hypersync.xyz/query
    HYPERSYNC_START_BLOCK  optional — scan floor (default 0; topic filter keeps
                      it cheap, but set to the allocator-era block to skip
                      pre-allocator history).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from typing import Any

import pandas as pd
import requests

_WAD = Decimal(10) ** 18
_VAT = "0x35d1b3f3d7966a1dfe207aa4514c12a259a0492b"
# Anonymous LogNote topic0 = 4-byte fn selector, left-aligned in the 32-byte word.
_FROB_T0 = "0x7608870300000000000000000000000000000000000000000000000000000000"
_GRAB_T0 = "0x7bab3f4000000000000000000000000000000000000000000000000000000000"
# dart (int256) sits at calldata byte 164; in the raw log `data` the note's
# `bytes` payload is preceded by two ABI words (offset + length = 64 bytes),
# so within raw data dart starts at byte 164 + 64 = 228.
_DART_PAYLOAD_OFFSET = 164
_DEFAULT_TIMEOUT = 40


class HyperSyncError(RuntimeError):
    """Raised on HyperSync transport / auth / query errors."""


def _endpoint() -> str:
    return os.environ.get("HYPERSYNC_URL", "https://eth.hypersync.xyz/query")


def _token() -> str:
    tok = os.environ.get("ENVIO_API_TOKEN")
    if not tok:
        raise HyperSyncError(
            "Missing env var ENVIO_API_TOKEN (free token at "
            "https://app.envio.dev/api-tokens; HyperSync returns 401 without it)"
        )
    return tok


def _to_int(v: Any) -> int:
    """HyperSync JSON returns numerics as hex strings ('0x..') or ints."""
    if isinstance(v, int):
        return v
    s = str(v)
    return int(s, 16) if s.startswith("0x") else int(s)


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
    """Implements ``IDebtSource`` via direct HyperSync raw-log queries.

    Injectable ``post`` for tests: any ``(url, json, headers, timeout) -> resp``
    with ``.ok``/``.status_code``/``.text``/``.json()``. Defaults to
    :func:`requests.post`.
    """

    def __init__(self, post: Callable[..., Any] = requests.post) -> None:
        self._post = post

    def debt_timeseries(self, ilk: bytes, start: date, pin_block: int) -> pd.DataFrame:
        cols = ["block_date", "daily_dart", "cum_debt"]
        rows = self._fetch_logs(ilk, pin_block)
        if not rows:
            return pd.DataFrame(columns=cols)

        df = pd.DataFrame(rows)
        df["block_date"] = df["ts"].apply(
            lambda t: datetime.fromtimestamp(int(t), tz=timezone.utc).date()
        )
        # Sum + cumsum in EXACT integer wad (Python ints), then ÷1e18 once at the
        # end under a wide Decimal context. Dividing per-row under the default
        # 28-digit context loses ~1e-6 wad vs Dune's DECIMAL(38,18); this matches
        # Dune byte-for-byte.
        daily = (
            df.groupby("block_date")["dart"].sum()        # exact int per day
            .reset_index(name="daily_wad")
            .sort_values("block_date")
            .reset_index(drop=True)
        )
        daily["cum_wad"] = daily["daily_wad"].cumsum()    # exact int cumulative
        with localcontext() as ctx:
            ctx.prec = 60
            daily["daily_dart"] = daily["daily_wad"].apply(lambda w: Decimal(int(w)) / _WAD)
            daily["cum_debt"] = daily["cum_wad"].apply(lambda w: Decimal(int(w)) / _WAD)
        return daily[cols]

    # -- transport ---------------------------------------------------------

    def _fetch_logs(self, ilk: bytes, pin_block: int) -> list[dict[str, Any]]:
        ilk_topic = "0x" + bytes(ilk).hex()
        from_block = int(os.environ.get("HYPERSYNC_START_BLOCK", "0"))
        to_block = pin_block + 1                     # HyperSync to_block is exclusive
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_token()}",
        }
        query_base = {
            "logs": [
                {
                    "address": [_VAT],
                    # topics[0] = topic0 (selector) OR-set; topics[1] = ilk.
                    "topics": [[_FROB_T0, _GRAB_T0], [ilk_topic]],
                }
            ],
            "field_selection": {
                "log": ["block_number", "log_index", "data", "topic0", "topic1"],
                "block": ["number", "timestamp"],
            },
        }

        out: list[dict[str, Any]] = []
        cursor = from_block
        guard = 0
        while cursor < to_block:
            guard += 1
            if guard > 100_000:                       # runaway backstop
                raise HyperSyncError("HyperSync pagination exceeded 100k pages")
            body = {**query_base, "from_block": cursor, "to_block": to_block}
            page = self._execute(body, headers)
            ts_by_block: dict[int, int] = {}
            groups = page.get("data") or []
            for g in groups:
                for b in g.get("blocks") or []:
                    ts_by_block[_to_int(b["number"])] = _to_int(b["timestamp"])
            for g in groups:
                for lg in g.get("logs") or []:
                    bn = _to_int(lg["block_number"])
                    out.append(
                        {"dart": _decode_dart(lg["data"]), "ts": ts_by_block.get(bn, 0)}
                    )
            nxt = page.get("next_block")
            if nxt is None or _to_int(nxt) <= cursor:  # no progress → done
                break
            cursor = _to_int(nxt)
        return out

    def _execute(self, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        try:
            resp = self._post(_endpoint(), json=body, headers=headers, timeout=_DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise HyperSyncError(f"HyperSync request failed: {exc}") from exc
        if not resp.ok:
            raise HyperSyncError(
                f"HyperSync -> HTTP {resp.status_code}: {resp.text[:400]}"
            )
        data: dict[str, Any] = resp.json()
        return data
