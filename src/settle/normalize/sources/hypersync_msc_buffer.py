"""HyperSync-direct extractor for the MSC leg (buffer basis) of Sky Net Revenue.

The MSC leg is settled in a single atomic transaction whose components (the
debt mint via Vat.grab on the three ALLOCATOR ilks, all USDS transfers to
prime subproxies, the Demand-side Buffer transfer, and the Core Council
Buffer Multisig transfer) fire in one block. **Month M's cycle is always
settled in month M+1** — the specific day varies across cycles (early to
late in M+1) but the M+1 rule holds. Mid-cycle capital events (Keel's $10M
seeding on 2026-03-30, small CC-only corrections) can also look
settlement-shaped on-chain but are NOT MSC settlements; they sit outside
the monthly cycle. To avoid confusing them, each report month is anchored
to its canonical settlement block in ``config/sky_total.yaml →
settlement_blocks``.

An auto-detect fallback exists (scan M+1 for USDS
``Transfer(from=0x0, to=CC_multisig)``) but is gated by the
``SKY_TOTAL_ALLOW_AUTODETECT=1`` env var — an earlier iteration picked
mid-cycle capital events instead of the real MSC settlement. Use only for
a brand-new cycle; back-fill the config anchor immediately after.

Cross-checked on June 2026 (block 25574490 @ 2026-07-20 14:21:59) — every
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

import os
from collections.abc import Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml

from ...extract import hypersync
from ._hypersync_common import _addr_topic, _evt, _row, _sel, _word
from .hypersync_debt import _decode_dart

__all__ = ["HyperSyncMscBufferSource", "load_config", "SettlementNotFoundError"]

_CHAIN = "ethereum"
_WAD = Decimal(10) ** 18

# ── addresses (lower-case) ──────────────────────────────────────────────────
_VAT = "0x35d1b3f3d7966a1dfe207aa4514c12a259a0492b"
_USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
_ZERO = "0x0000000000000000000000000000000000000000"


_GRAB = "0x7bab3f4000000000000000000000000000000000000000000000000000000000"
_TRANSFER = _evt("Transfer(address,address,uint256)")

# Self-check.
assert _sel("grab(bytes32,address,address,address,int256,int256)") == _GRAB


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
        "settlement_blocks": {
            # ``null`` = "no MSC settlement executed in this month" (only
            # 2026-01 under the execution-month bucketing) — the source
            # emits a zero MSC leg instead of auto-detecting.
            k: (None if v is None else int(v))
            for k, v in (cfg.get("settlement_blocks") or {}).items()
        },
        "grove_tge_penalty": {
            k: (None if v is None else Decimal(str(v)))
            for k, v in (cfg.get("grove_tge_penalty") or {}).items()
        },
        "one_off_transfers": {
            month: {prime: Decimal(str(amt)) for prime, amt in (byprime or {}).items()}
            for month, byprime in (cfg.get("one_off_transfers") or {}).items()
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
        """Locate month M's settlement block, extract the seven buffer-basis
        components from that block.

        Preference order:
          1. ``config/sky_total.yaml → settlement_blocks[YYYY-MM]`` if present
             (per-month explicit anchor, the audited path for validated
             months).
          2. Auto-detect fallback: latest USDS ``Transfer(from=0x0, to=CC)``
             in month M+1. Vulnerable to picking up the WRONG cycle when the
             settlement executes in M itself (MSC#7, MSC#8, …) — use only
             for freshly-landed cycles before back-filling the config.
        """
        label = f"{month.year}-{month.month:02d}"
        blocks_cfg = self._cfg.get("settlement_blocks", {})
        override = blocks_cfg.get(label)
        if label in blocks_cfg and override is None:
            # Explicit null: no MSC settlement executed in this month
            # (execution-month bucketing; 2026-01). Zero MSC leg.
            return pd.DataFrame(
                [_row("settlement_block", "0", 0),
                 _row("settlement_ts", "0", 0)],
                columns=["stream", "label", "amount"],
            )
        if override is not None:
            if override > pin_block:
                raise SettlementNotFoundError(
                    f"month {label}: configured settlement_block {override} is "
                    f"past pin_block {pin_block} — archive not caught up yet"
                )
            # Discover the block's timestamp via any log in it (the CC-mint
            # signature that anchored this block in the first place will
            # always fire, so it's a reliable source).
            cc = self._cfg["core_council_multisig"]
            rows = hypersync.query_logs(
                _CHAIN,
                [{"address": [_USDS], "topics": [
                    [_TRANSFER], [_addr_topic(_ZERO)], [_addr_topic(cc)]]}],
                override, override, post=self._post,
            ).rows
            if not rows:
                raise SettlementNotFoundError(
                    f"month {label}: configured settlement_block {override} "
                    "does not contain the expected USDS mint(from=0x0, to=CC) "
                    "signature. Cross-check the block number in config."
                )
            settlement_block, settlement_ts = override, rows[0].block_time
        elif os.environ.get("SKY_TOTAL_ALLOW_AUTODETECT") == "1":
            # Opt-in escape hatch. Even though month M's MSC settlement is
            # always in M+1, the fallback can still latch onto a
            # mid-cycle capital event (Mar-30's Keel seeding, Mar-06's
            # CC-only correction) if one lands in M+1 later than the real
            # settlement. Use only for a brand-new cycle before
            # back-filling the config, and expect to `git blame` this run
            # when audit questions land.
            start_ts, end_ts = _next_month_range(month)
            fb = hypersync.find_block_at_or_before(_CHAIN, start_ts)
            tb = min(pin_block, hypersync.find_block_at_or_before(_CHAIN, end_ts))
            if tb < fb:
                raise SettlementNotFoundError(
                    f"month {label}: pin_block {pin_block} is before the start "
                    f"of the following month ({start_ts}) — the settlement "
                    "hasn't happened yet"
                )
            settlement_block, settlement_ts = self._find_settlement(fb, tb, start_ts, end_ts)
        else:
            raise SettlementNotFoundError(
                f"month {label}: no settlement_block in config/sky_total.yaml. "
                "MSC settlements are always in M+1 but the day varies, and "
                "mid-cycle capital events can masquerade as settlements — so "
                "we don't auto-detect by default. Fix: add an entry under "
                f"`settlement_blocks: {label!r}: <block_number>` in config, or "
                "set SKY_TOTAL_ALLOW_AUTODETECT=1 to opt into the fallback for "
                "a freshly-landed cycle."
            )

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

        return [_row(stream, dst, totals[stream]) for dst, stream in dst_to_stream.items()]
