"""HyperSync-direct extractor for the MSC leg (buffer basis) of Sky Net Revenue.

The MSC leg is settled in a single atomic transaction that fires **in month
M+1**, not in month M itself: the settlement block for month M's cycle
contains the debt mint (Vat.grab on the three ALLOCATOR ilks), all USDS
transfers to prime subproxies, the transfer to the Demand-side Buffer, and
the transfer to the Core Council Buffer Multisig. This extractor auto-detects
that block and reads the whole set of components from it.

Settlement-block detection: for month M we scan month M+1 for USDS
``Transfer(from=0x0, to=CC_multisig)`` — a signature that has fired at every
MSC settlement since MSC#5 (Jan 2026, dated 2026-02-02). The latest such
transfer in M+1 is the settlement block for M's cycle. This detection was
cross-checked on June 2026 (block 25574490 @ 2026-07-20 14:21:59) — every
transfer amount ties to the methodology doc §3 to the dollar for all seven
non-mint components; the Spark mint is ~4% shy of the doc's figure (open
item — see the summary's warning).

Emitted stream rows (same ``[stream, label, amount]`` idiom as
``HyperSyncNonMscSource``):

    stream                label                  amount
    settlement_block      <block number>         <block>          (metadata)
    settlement_ts         <unix ts>              <ts>             (metadata)
    mint:spark            ALLOCATOR-SPARK-A      Σ grab dart (USDS)
    mint:grove            ALLOCATOR-BLOOM-A      Σ grab dart (USDS)
    mint:obex             ALLOCATOR-OBEX-A       Σ grab dart (USDS)
    subproxy:spark        <subproxy addr>        Σ USDS mint to subproxy
    subproxy:grove        ...                    ...
    subproxy:obex         ...                    ...
    subproxy:keel         ...                    ...
    subproxy:skybase      ...                    ...
    dsb                   <DSB addr>             Σ USDS mint to DSB
    cc                    <CC addr>              Σ USDS mint to CC (GROSS —
                                                 includes both the genesis
                                                 repayment and the Step 1
                                                 Capital 20% distribution;
                                                 the compute layer splits
                                                 these algebraically)

The Grove TGE penalty is NOT emitted here — its on-chain mechanism is "still
open with BA" (PRD §17.13 B16); the compute layer reads it from a per-month
override in ``config/sky_total.yaml``.

Config (env):
    ENVIO_API_TOKEN   required — free token from https://app.envio.dev/api-tokens
    DATABASE_URL      optional — enables the reorg-safe log store
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml

from ...extract import hypersync
from ...extract._keccak import keccak256
from .hypersync_debt import _decode_dart

__all__ = ["HyperSyncMscBufferSource", "load_config", "SettlementNotFoundError"]

_CHAIN = "ethereum"
_WAD = Decimal(10) ** 18

# ── addresses (lower-case) ──────────────────────────────────────────────────
_VAT = "0x35d1b3f3d7966a1dfe207aa4514c12a259a0492b"
_USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
_ZERO = "0x0000000000000000000000000000000000000000"


# ── topic0 helpers ──────────────────────────────────────────────────────────
def _sel(sig: str) -> str:
    """LogNote topic0 — 4-byte fn selector left-aligned in a 32-byte word."""
    return "0x" + keccak256(sig.encode()).hex()[:8] + "0" * 56


def _evt(sig: str) -> str:
    """Real (non-anonymous) event topic0 — full keccak of the signature."""
    return "0x" + keccak256(sig.encode()).hex()


_GRAB = "0x7bab3f4000000000000000000000000000000000000000000000000000000000"
_TRANSFER = _evt("Transfer(address,address,uint256)")

# Self-check.
assert _sel("grab(bytes32,address,address,address,int256,int256)") == _GRAB


def _addr_topic(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().replace("0x", "")


def _word(data_hex: str, idx: int) -> int:
    """Return 32-byte word ``idx`` of a ``data`` blob as an unsigned int."""
    raw = data_hex[2:] if data_hex.startswith("0x") else data_hex
    return int(raw[idx * 64 : idx * 64 + 64] or "0", 16)


def _row(stream: str, label: str, amount: Any) -> dict[str, Any]:
    return {"stream": stream, "label": label, "amount": amount}


class SettlementNotFoundError(Exception):
    """Raised when no MSC settlement block is found in month M+1 for month M."""


# ── config loading ──────────────────────────────────────────────────────────

_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "sky_total.yaml"
)


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load ``config/sky_total.yaml`` and lower-case every address."""
    cfg = yaml.safe_load((path or _DEFAULT_CONFIG_PATH).read_text())
    return {
        "allocator_ilks": {k: v.lower() for k, v in cfg["allocator_ilks"].items()},
        "subproxies": {k: v.lower() for k, v in cfg["subproxies"].items()},
        "demand_side_buffer": cfg["demand_side_buffer"].lower(),
        "core_council_multisig": cfg["core_council_multisig"].lower(),
        "grove_tge_penalty": {
            k: (None if v is None else Decimal(str(v)))
            for k, v in (cfg.get("grove_tge_penalty") or {}).items()
        },
    }


def _next_month_range(month: Any) -> tuple[int, int]:
    """Return (start_ts, end_ts) UTC unix timestamps for month M+1."""
    if month.month == 12:
        start = date(month.year + 1, 1, 1)
        end_excl = date(month.year + 1, 2, 1)
    elif month.month == 11:
        start = date(month.year, 12, 1)
        end_excl = date(month.year + 1, 1, 1)
    else:
        start = date(month.year, month.month + 1, 1)
        end_excl = date(month.year, month.month + 2, 1)
    return (
        int(datetime.combine(start, time.min, tzinfo=timezone.utc).timestamp()),
        int(datetime.combine(end_excl, time.min, tzinfo=timezone.utc).timestamp()),
    )


# ── source ──────────────────────────────────────────────────────────────────

class HyperSyncMscBufferSource:
    """Emits MSC buffer-basis stream rows from the M+1 settlement block."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        post: Callable[..., Any] = requests.post,
    ) -> None:
        self._cfg = config or load_config()
        self._post = post

    # -- public --------------------------------------------------------------

    def streams(self, month: Any, pin_block: int) -> pd.DataFrame:
        """Locate month M's settlement block (fires in M+1), extract the seven
        buffer-basis components from that block. ``pin_block`` is a safety
        ceiling — if it's before the M+1 settlement (i.e., the settlement
        hasn't happened yet), raise ``SettlementNotFoundError``.
        """
        start_ts, end_ts = _next_month_range(month)
        # Cap the M+1 scan to the pin_block so a mid-cycle re-run doesn't
        # accidentally pick up a future settlement.
        fb = hypersync.find_block_at_or_before(_CHAIN, start_ts)
        tb = min(pin_block, hypersync.find_block_at_or_before(_CHAIN, end_ts))
        if tb < fb:
            raise SettlementNotFoundError(
                f"month {month}: pin_block {pin_block} is before the start of "
                f"the following month ({start_ts}) — the settlement hasn't "
                "happened yet"
            )

        settlement_block, settlement_ts = self._find_settlement(fb, tb, start_ts, end_ts)

        rows: list[dict[str, Any]] = [
            _row("settlement_block", str(settlement_block), settlement_block),
            _row("settlement_ts", str(settlement_ts), settlement_ts),
        ]
        rows += self._mints(settlement_block)
        rows += self._usds_receivers(settlement_block)
        return pd.DataFrame(rows, columns=["stream", "label", "amount"])

    # -- settlement-block auto-detect --------------------------------------

    def _find_settlement(
        self, fb: int, tb: int, start_ts: int, end_ts: int
    ) -> tuple[int, int]:
        """Return (block, block_time) of the MSC settlement in month M+1.

        Signature: USDS Transfer(from=0x0, to=CC_multisig). CC has received a
        mint at every MSC settlement observed on-chain (Feb 2026 → present),
        so this is the reliable detector. If more than one such event lands
        in the same month M+1, take the LATEST (which corresponds to the
        current cycle rather than a prior-month catch-up — see MSC#7 in
        March 2026 for a two-in-one-month example).
        """
        cc = self._cfg["core_council_multisig"]
        rows = hypersync.query_logs(
            _CHAIN,
            [{"address": [_USDS], "topics": [
                [_TRANSFER], [_addr_topic(_ZERO)], [_addr_topic(cc)]]}],
            fb, tb, post=self._post,
        ).rows
        in_month = [r for r in rows if start_ts <= r.block_time < end_ts]
        if not in_month:
            raise SettlementNotFoundError(
                f"no USDS mint(from=0x0, to=CC {cc}) found in blocks {fb}..{tb} "
                f"(month range [{start_ts}, {end_ts})). The settlement has not "
                "landed yet; re-run once it does."
            )
        # Pick the LATEST — the current cycle's settlement.
        winner = max(in_month, key=lambda r: r.block_number)
        return winner.block_number, winner.block_time

    # -- mints: Σ Vat.grab dart per allocator ilk in the settlement block --

    def _mints(self, block: int) -> list[dict]:
        """Sum of GRAB dart on each allocator ilk in the settlement block.

        (FROB isn't used at MSC settlement — the allocator vaults transfer
        debt via GRAB. Verified on 2026-07-20 block 25574490.)
        """
        out: list[dict] = []
        for prime, ilk_bytes32 in self._cfg["allocator_ilks"].items():
            total_wad = 0
            rows = hypersync.query_logs(
                _CHAIN,
                [{"address": [_VAT], "topics": [[_GRAB], [ilk_bytes32]]}],
                block, block, post=self._post,
            ).rows
            for r in rows:
                total_wad += _decode_dart(r.data)
            out.append(
                _row(f"mint:{prime}", ilk_bytes32, Decimal(total_wad) / _WAD)
            )
        return out

    # -- USDS mints (from=0x0) to subproxies / DSB / CC --------------------

    def _usds_receivers(self, block: int) -> list[dict]:
        """USDS Transfer(from=0x0, to=X) on the settlement block, bucketed by
        destination. Using from=0x0 (mint) precisely captures the settlement
        outflows and excludes secondary noise (e.g. subproxy-to-subproxy
        transfers).
        """
        sub = self._cfg["subproxies"]
        dsb = self._cfg["demand_side_buffer"]
        cc = self._cfg["core_council_multisig"]
        dst_to_stream: dict[str, str] = {
            **{v: f"subproxy:{k}" for k, v in sub.items()},
            dsb: "dsb",
            cc: "cc",
        }
        dst_labels = {**{v: v for v in dst_to_stream}}
        # Preseed all streams with $0 so a subproxy with no receipt still
        # appears in the output.
        totals: dict[str, Decimal] = {stream: Decimal(0) for stream in dst_to_stream.values()}

        rows = hypersync.query_logs(
            _CHAIN,
            [{"address": [_USDS], "topics": [
                [_TRANSFER],
                [_addr_topic(_ZERO)],
                [_addr_topic(a) for a in dst_to_stream],
            ]}],
            block, block, post=self._post,
        ).rows
        for r in rows:
            dst = "0x" + r.topic2[-40:].lower()
            stream = dst_to_stream.get(dst)
            if stream is None:
                continue
            totals[stream] += Decimal(_word(r.data, 0)) / _WAD

        out: list[dict] = []
        for dst, stream in dst_to_stream.items():
            out.append(_row(stream, dst_labels[dst], totals[stream]))
        return out
