"""HyperSync extractor for the Smart Burn Engine's monthly activity.

One ``Kicker.flap()`` pulls ``kbump`` USDS from the surplus and calls
``Splitter.kick(tot, 0)``, which emits ``Kick(tot, lot, pay)`` — ``lot`` goes
to the Flapper (``Exec(lot, bought)`` — a Uniswap v2 swap whose SKY lands in
the receiver, the Pause Proxy) and ``pay`` to REWARDS_LSSKY_USDS
(``RewardAdded(pay)``). All three events fire in the same transaction, so a
kick is reconstructed by joining ``Kick`` and ``Exec`` on the tx hash.

The source also captures the month's parameter changes (``File`` on the
Splitter / Kicker / distributor, ``Init``/``Yank`` on the vest) so every kick
carries the ``burn`` and ``hop`` in force when it ran — the burn attribution
(10/55 of SKY bought under the 55% regime) keys off that — and the
distributor's ``Distribute`` pulls that top up the SKY farm.

Free-tier RPCs cap ``eth_getLogs`` at 10 blocks; a month is ~215k blocks, so
this goes through HyperSync (``ENVIO_API_TOKEN``) like the other log-based
sources. State reads (``burn``/``hop`` at the month start, the month-end
snapshot) are ``eth_call`` and live in ``extract/tmf_state.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import requests

from ...compute.tmf import SbeActivity, SbeDistribution, SbeKick, SbeParamChange
from ...extract import hypersync as hs
from ...extract.hypersync import LogRow
from ._hypersync_common import _evt, _word

__all__ = ["HyperSyncSbeSource", "month_block_range"]

WAD = Decimal(10) ** 18
RAD = Decimal(10) ** 45

_KICK = _evt("Kick(uint256,uint256,uint256)")        # Splitter
_EXEC = _evt("Exec(uint256,uint256)")                # FlapperUniV2SwapOnly
_FILE_U = _evt("File(bytes32,uint256)")              # Splitter hop/burn, Kicker kbump, dist vestId
_FILE_I = _evt("File(bytes32,int256)")               # Kicker khump
_DISTRIBUTE = _evt("Distribute(uint256)")            # REWARDS_DIST_LSSKY_SKY
_VEST_INIT = _evt("Init(uint256,address)")           # DssVest
_VEST_YANK = _evt("Yank(uint256,uint256)")
_REWARDS_DURATION = _evt("RewardsDurationUpdated(uint256)")  # StakingRewards

_LOG_FIELDS = [
    "block_number", "log_index", "address",
    "topic0", "topic1", "topic2", "topic3", "data", "transaction_hash",
]


def _what(topic1: str | None) -> str:
    """Decode an indexed ``bytes32 what`` into its ASCII name."""
    if not topic1:
        return ""
    return bytes.fromhex(topic1[2:]).rstrip(b"\0").decode("ascii", errors="replace")


def _signed(v: int) -> int:
    return v - (1 << 256) if v >= (1 << 255) else v


def month_block_range(month: Any) -> tuple[int, int, int, int]:
    """``(from_block, to_block, from_ts, to_ts)`` for a calendar month —
    first block after the prior month's EoD pin through this month's EoD
    (23:59:59 UTC) block, resolved via HyperSync's block-timestamp search."""
    y, m = int(month.year), int(month.month)
    start = datetime(y, m, 1, tzinfo=UTC)
    end = datetime(y + (m // 12), (m % 12) + 1, 1, tzinfo=UTC)
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp()) - 1
    prev_eod = hs.find_block_at_or_before("ethereum", start_ts - 1)
    to_block = hs.find_block_at_or_before("ethereum", end_ts)
    return prev_eod + 1, to_block, start_ts, end_ts


class HyperSyncSbeSource:
    def __init__(
        self,
        contracts: dict[str, str],
        *,
        post: Callable[..., Any] = requests.post,
    ) -> None:
        self.c = {k: v.lower() for k, v in contracts.items()}
        self._post = post
        self._by_addr = {v: k for k, v in self.c.items()}

    # ── public ──

    def activity(
        self,
        month: Any,
        from_block: int,
        to_block: int,
        *,
        from_ts: int,
        to_ts: int,
        burn_at_start: Decimal,
        hop_at_start: int,
    ) -> SbeActivity:
        """Decode the month's kicks / parameter changes / distributions.

        ``burn_at_start`` and ``hop_at_start`` are the Splitter's values at
        ``from_block - 1`` (state read) — the walk below applies each ``File``
        in log order so every kick records the parameters in force."""
        rows = hs.query_logs(
            "ethereum",
            [{"address": [
                self.c["MCD_SPLIT"], self.c["MCD_FLAP"], self.c["MCD_KICK"],
                self.c["REWARDS_DIST_LSSKY_SKY"], self.c["MCD_VEST_SKY_TREASURY"],
                self.c["REWARDS_LSSKY_USDS"],
            ]}],
            from_block, to_block,
            log_fields=_LOG_FIELDS,
            post=self._post,
        ).rows
        rows.sort(key=lambda r: (r.block_number, r.log_index))
        return self._decode(month, rows, from_block, to_block, from_ts, to_ts,
                            burn_at_start, hop_at_start)

    # ── decoding ──

    def _decode(
        self, month: Any, rows: list[LogRow], from_block: int, to_block: int,
        from_ts: int, to_ts: int, burn: Decimal, hop: int,
    ) -> SbeActivity:
        act = SbeActivity(
            month=str(month), from_block=from_block, to_block=to_block,
            from_ts=from_ts, to_ts=to_ts,
        )
        execs: dict[str, Decimal] = {}          # tx → SKY bought
        kicks_raw: list[tuple[LogRow, Decimal, Decimal, Decimal, Decimal, int]] = []

        for r in rows:
            key = self._by_addr.get(r.address, r.address)
            tx = r.transaction_hash or ""
            if r.topic0 == _KICK and key == "MCD_SPLIT":
                tot = Decimal(_word(r.data, 0)) / RAD
                lot = Decimal(_word(r.data, 1)) / WAD
                pay = Decimal(_word(r.data, 2)) / WAD
                kicks_raw.append((r, tot, lot, pay, burn, hop))
            elif r.topic0 == _EXEC and key == "MCD_FLAP":
                execs[tx] = execs.get(tx, Decimal(0)) + Decimal(_word(r.data, 1)) / WAD
            elif r.topic0 in (_FILE_U, _FILE_I):
                what = _what(r.topic1)
                raw = _word(r.data, 0)
                value: Decimal
                if key == "MCD_SPLIT" and what == "burn":
                    value = Decimal(raw) / WAD
                    burn = value
                elif key == "MCD_SPLIT" and what == "hop":
                    value = Decimal(raw)
                    hop = raw
                elif key == "MCD_KICK" and what == "kbump":
                    value = Decimal(raw) / RAD
                elif key == "MCD_KICK" and what == "khump":
                    value = Decimal(_signed(raw)) / RAD
                elif key == "REWARDS_DIST_LSSKY_SKY" and what == "vestId":
                    value = Decimal(raw)
                else:
                    value = Decimal(_signed(raw)) if r.topic0 == _FILE_I else Decimal(raw)
                act.param_changes.append(SbeParamChange(
                    block=r.block_number, log_index=r.log_index, ts=r.block_time, tx=tx,
                    contract=key, what=what or "?", value=value,
                ))
            elif r.topic0 == _REWARDS_DURATION and key == "REWARDS_LSSKY_USDS":
                act.param_changes.append(SbeParamChange(
                    block=r.block_number, log_index=r.log_index, ts=r.block_time, tx=tx,
                    contract=key, what="rewardsDuration", value=Decimal(_word(r.data, 0)),
                ))
            elif r.topic0 == _VEST_INIT and key == "MCD_VEST_SKY_TREASURY":
                act.param_changes.append(SbeParamChange(
                    block=r.block_number, log_index=r.log_index, ts=r.block_time, tx=tx,
                    contract=key, what="vest.init (id)", value=Decimal(int(r.topic1 or "0x0", 16)),
                ))
            elif r.topic0 == _VEST_YANK and key == "MCD_VEST_SKY_TREASURY":
                act.param_changes.append(SbeParamChange(
                    block=r.block_number, log_index=r.log_index, ts=r.block_time, tx=tx,
                    contract=key, what="vest.yank (id)", value=Decimal(int(r.topic1 or "0x0", 16)),
                ))
            elif r.topic0 == _DISTRIBUTE and key == "REWARDS_DIST_LSSKY_SKY":
                act.distributions.append(SbeDistribution(
                    block=r.block_number, ts=r.block_time, tx=tx,
                    amount=Decimal(_word(r.data, 0)) / WAD,
                ))

        for r, tot, lot, pay, b, h in kicks_raw:
            tx = r.transaction_hash or ""
            bought = execs.get(tx, Decimal(0))
            if lot > 0 and bought == 0:
                raise ValueError(
                    f"hypersync_sbe: Splitter Kick in tx {tx} at block {r.block_number} sent "
                    f"{lot} USDS to the Flapper but no Exec event was found in the same tx"
                )
            act.kicks.append(SbeKick(
                block=r.block_number, log_index=r.log_index, ts=r.block_time, tx=tx,
                tot=tot, lot=lot, pay=pay, bought=bought, burn=b, hop=h,
            ))
        return act
