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

import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import requests

from ...domain.tmf import SbeActivity, SbeDistribution, SbeKick, SbeParamChange, SkyBurn
from ...extract import hypersync as hs
from ...extract import hypersync_store
from ...extract.hypersync import LogRow
from ._hypersync_common import _addr_topic, _evt, _word

__all__ = ["HyperSyncSbeSource", "MonthNotClosedError", "month_block_range"]

WAD = Decimal(10) ** 18
RAD = Decimal(10) ** 45

_KICK = _evt("Kick(uint256,uint256,uint256)")        # Splitter
_EXEC = _evt("Exec(uint256,uint256)")                # FlapperUniV2SwapOnly
_FILE_U = _evt("File(bytes32,uint256)")              # Splitter hop/burn, Kicker kbump, dist vestId
_FILE_I = _evt("File(bytes32,int256)")               # Kicker khump
_FILE_A = _evt("File(bytes32,address)")              # Splitter flapper/farm, Flapper pip, dist
_DISTRIBUTE = _evt("Distribute(uint256)")            # REWARDS_DIST_LSSKY_SKY
_VEST_INIT = _evt("Init(uint256,address)")           # DssVest
_VEST_YANK = _evt("Yank(uint256,uint256)")
_REWARDS_DURATION = _evt("RewardsDurationUpdated(uint256)")  # StakingRewards
_TRANSFER = _evt("Transfer(address,address,uint256)")

_TOPICS = [
    _KICK, _EXEC, _FILE_U, _FILE_I, _FILE_A, _DISTRIBUTE,
    _VEST_INIT, _VEST_YANK, _REWARDS_DURATION,
]

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


# (chainlog key, what) → divisor for File(bytes32,uint256|int256) values, so
# every SbeParamChange.value is in human units. Anything not listed is kept
# RAW and labelled "<what> (raw)" so a new lever can never render as a
# plausible-looking scaled number.
_FILE_SCALE: dict[tuple[str, str], Decimal] = {
    ("MCD_SPLIT", "hop"): Decimal(1),
    ("MCD_SPLIT", "burn"): WAD,
    ("MCD_KICK", "kbump"): RAD,
    ("MCD_KICK", "khump"): RAD,
    ("MCD_FLAP", "want"): WAD,
    ("MCD_VEST_SKY_TREASURY", "cap"): WAD,
    ("REWARDS_DIST_LSSKY_SKY", "vestId"): Decimal(1),
}


class MonthNotClosedError(RuntimeError):
    """The requested month has not ended yet — the report would silently
    describe a partial month as if it were complete."""


def month_block_range(
    month: Any, *, allow_partial: bool = False, now: float | None = None
) -> tuple[int, int, int, int, bool]:
    """``(from_block, to_block, from_ts, to_ts, partial)`` for a calendar month —
    first block after the prior month's EoD pin through this month's EoD
    (23:59:59 UTC) block, resolved via HyperSync's block-timestamp search.

    A month that has not closed yet raises ``MonthNotClosedError`` — the
    resolver would otherwise head-clamp silently and the report would
    describe a partial month as if it were complete. ``allow_partial=True``
    instead returns the archive head as ``to_block`` with its real timestamp
    as ``to_ts`` and ``partial=True`` so the report can say so."""
    y, m = int(month.year), int(month.month)
    start = datetime(y, m, 1, tzinfo=UTC)
    end = datetime(y + (m // 12), (m % 12) + 1, 1, tzinfo=UTC)
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp()) - 1
    end_dt = f"{datetime.fromtimestamp(end_ts, UTC):%Y-%m-%d %H:%M:%S} UTC"
    if end_ts >= (time.time() if now is None else now) and not allow_partial:
        raise MonthNotClosedError(
            f"{month}: month-end {end_dt} is in the future — pass --allow-partial to "
            "report the month-to-date."
        )
    prev_eod = hs.find_block_at_or_before("ethereum", start_ts - 1)
    to_block = hs.find_block_at_or_before("ethereum", end_ts)
    # ``partial`` is decided from the RESOLVED block, not the wall clock: when
    # the archive head lags month-end the resolver head-clamps (warning only),
    # and a report built on that block would describe a truncated month as
    # complete. Any month whose end block cannot yet be served is partial.
    to_ts = hs.block_timestamp("ethereum", to_block)
    partial = to_ts < end_ts - 60          # > one block-time short of month-end
    if partial and not allow_partial:
        raise MonthNotClosedError(
            f"{month}: the HyperSync archive head (block {to_block}, "
            f"{datetime.fromtimestamp(to_ts, UTC):%Y-%m-%d %H:%M:%S} UTC) has not reached "
            f"month-end {end_dt} — retry once it catches up, or pass --allow-partial."
        )
    return prev_eod + 1, to_block, start_ts, (to_ts if partial else end_ts), partial


class HyperSyncSbeSource:
    def __init__(
        self,
        contracts: dict[str, str],
        *,
        flappers: list[str] | None = None,
        post: Callable[..., Any] = requests.post,
    ) -> None:
        """``flappers``: every Flapper the Splitter has ever pointed at (the
        history run spans a Flapper swap); each maps to the ``MCD_FLAP`` role.
        Defaults to the current ``contracts["MCD_FLAP"]`` only."""
        self.c = {k: v.lower() for k, v in contracts.items()}
        self._post = post
        self._by_addr = {v: k for k, v in self.c.items()}
        self._flappers = sorted({self.c["MCD_FLAP"], *(f.lower() for f in flappers or [])})
        for f in self._flappers:
            self._by_addr.setdefault(f, "MCD_FLAP")

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
            [{
                "address": [
                    self.c["MCD_SPLIT"], self.c["MCD_FLAP"], self.c["MCD_KICK"],
                    self.c["REWARDS_DIST_LSSKY_SKY"], self.c["MCD_VEST_SKY_TREASURY"],
                    self.c["REWARDS_LSSKY_USDS"],
                ],
                # topic0 filter — without it every Staked/Withdrawn/RewardPaid
                # log of both farms (thousands a month) is fetched and dropped.
                "topics": [_TOPICS],
            }],
            from_block, to_block,
            log_fields=_LOG_FIELDS,
            post=self._post,
        ).rows
        rows.sort(key=lambda r: (r.block_number, r.log_index))
        return self._decode(month, rows, from_block, to_block, from_ts, to_ts,
                            burn_at_start, hop_at_start)

    def history(
        self,
        from_block: int,
        to_block: int,
        *,
        fetch: Callable[..., list[LogRow]] = hypersync_store.fetch_logs,
    ) -> SbeActivity:
        """Every Splitter kick and parameter change in ``[from_block, to_block]``
        — the dashboard's full history. Goes through the reorg-safe log store
        (incremental on re-runs when ``DATABASE_URL`` is set). Start at the
        Splitter's deployment block: its initial ``File`` events set burn / hop
        / flapper / farm, so no state read is needed."""
        rows = fetch(
            "ethereum",
            [{
                "address": [self.c["MCD_SPLIT"], self.c["MCD_KICK"], *self._flappers],
                "topics": [[_KICK, _EXEC, _FILE_U, _FILE_I, _FILE_A]],
            }],
            from_block, to_block,
            log_fields=_LOG_FIELDS,
            post=self._post,
        )
        rows = sorted(rows, key=lambda r: (r.block_number, r.log_index))
        from_ts = rows[0].block_time if rows else 0
        to_ts = rows[-1].block_time if rows else 0
        return self._decode("history", rows, from_block, to_block, from_ts, to_ts,
                            Decimal(0), 0)

    def sky_burns(
        self,
        from_block: int,
        to_block: int,
        *,
        sinks: dict[str, str],
        protocol_senders: list[str],
        fetch: Callable[..., list[LogRow]] = hypersync_store.fetch_logs,
    ) -> list[SkyBurn]:
        """SKY ``Transfer``s into burn sinks. ``sinks['dead']`` counts from ANY
        sender (``protocol`` flags the protocol ones); ``sinks['zero']`` only
        from ``protocol_senders`` — ``SKY.burn()`` is also how the MKR↔SKY
        converter retires SKY, which is a conversion, not a treasury burn."""
        sky = self.c["SKY"]
        senders = [s.lower() for s in protocol_senders]
        dead = sinks["dead"].lower()
        zero = sinks["zero"].lower()
        selections: list[dict[str, Any]] = [
            {"address": [sky], "topics": [[_TRANSFER], [], [_addr_topic(dead)]]},
            {"address": [sky], "topics": [[_TRANSFER], [_addr_topic(s) for s in senders],
                                          [_addr_topic(zero)]]},
        ]
        rows = fetch("ethereum", selections, from_block, to_block,
                     log_fields=_LOG_FIELDS, post=self._post)
        out: list[SkyBurn] = []
        for r in sorted(rows, key=lambda r: (r.block_number, r.log_index)):
            if r.topic0 != _TRANSFER or not r.topic1 or not r.topic2:
                continue
            sender = "0x" + r.topic1[-40:]
            sink = "0x" + r.topic2[-40:]
            if sink not in (dead, zero):
                continue
            if sink == zero and sender not in senders:
                continue
            if r.transaction_hash is None:
                raise ValueError(
                    f"hypersync_sbe: SKY burn log at block {r.block_number} has no "
                    "transaction_hash"
                )
            out.append(SkyBurn(
                block=r.block_number, log_index=r.log_index, ts=r.block_time,
                tx=r.transaction_hash, sender=sender, sink=sink,
                amount=Decimal(_word(r.data, 0)) / WAD, protocol=sender in senders,
            ))
        return out

    # ── decoding ──

    def _decode(
        self, month: Any, rows: list[LogRow], from_block: int, to_block: int,
        from_ts: int, to_ts: int, burn: Decimal, hop: int,
    ) -> SbeActivity:
        act = SbeActivity(
            month=str(month), from_block=from_block, to_block=to_block,
            from_ts=from_ts, to_ts=to_ts,
        )
        execs: dict[str, list[tuple[int, Decimal]]] = {}   # tx → [(log_index, SKY bought)]
        kicks_raw: list[tuple[LogRow, Decimal, Decimal, Decimal, Decimal, int, str | None, str | None]] = []
        farm: str | None = None
        flapper: str | None = None

        for r in rows:
            key = self._by_addr.get(r.address, r.address)
            if r.transaction_hash is None:
                # The Exec→Kick join is keyed on the tx hash; a missing one
                # would collapse rows into a shared bucket and pair across
                # unrelated transactions.
                raise ValueError(
                    f"hypersync_sbe: log at block {r.block_number} index {r.log_index} "
                    f"({key}) has no transaction_hash — refusing to join events"
                )
            tx = r.transaction_hash
            if r.topic0 == _KICK and key == "MCD_SPLIT":
                tot = Decimal(_word(r.data, 0)) / RAD
                lot = Decimal(_word(r.data, 1)) / WAD
                pay = Decimal(_word(r.data, 2)) / WAD
                kicks_raw.append((r, tot, lot, pay, burn, hop, farm, flapper))
            elif r.topic0 == _EXEC and key == "MCD_FLAP":
                execs.setdefault(tx, []).append((r.log_index, Decimal(_word(r.data, 1)) / WAD))
            elif r.topic0 in (_FILE_U, _FILE_I):
                what = _what(r.topic1)
                raw = _signed(_word(r.data, 0)) if r.topic0 == _FILE_I else _word(r.data, 0)
                scale = _FILE_SCALE.get((key, what))
                if scale is None:
                    label, value = f"{what or '?'} (raw)", Decimal(raw)
                else:
                    label, value = what, Decimal(raw) / scale
                if key == "MCD_SPLIT" and what == "burn":
                    burn = value
                elif key == "MCD_SPLIT" and what == "hop":
                    hop = raw
                act.param_changes.append(SbeParamChange(
                    block=r.block_number, log_index=r.log_index, ts=r.block_time, tx=tx,
                    contract=key, what=label, value=value,
                ))
            elif r.topic0 == _FILE_A:
                what = _what(r.topic1)
                addr = "0x" + r.data[-40:]
                if key == "MCD_SPLIT" and what == "farm":
                    farm = addr
                elif key == "MCD_SPLIT" and what == "flapper":
                    flapper = addr
                act.param_changes.append(SbeParamChange(
                    block=r.block_number, log_index=r.log_index, ts=r.block_time, tx=tx,
                    contract=key, what=what or "?", value=addr,
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

        # Join Exec → Kick POSITIONALLY within a transaction. Splitter.kick()
        # calls flapper.exec() (which emits Exec) and then emits Kick last, so
        # a Kick's Exec is the nearest unconsumed Exec BEFORE its log index.
        # A tx with several kicks (hop = 0, or a multicall keeper) therefore
        # never double-counts one Exec.
        for r, tot, lot, pay, b, h, fm, fl in kicks_raw:
            tx = r.transaction_hash or ""     # validated non-None above
            bought = Decimal(0)
            if lot > 0:
                pending = execs.get(tx, [])
                before = [i for i, (li, _) in enumerate(pending) if li < r.log_index]
                if not before:
                    raise ValueError(
                        f"hypersync_sbe: Splitter Kick in tx {tx} at block {r.block_number} sent "
                        f"{lot} USDS to the Flapper but no Exec event precedes it in the same tx"
                    )
                _, bought = pending.pop(before[-1])
            act.kicks.append(SbeKick(
                block=r.block_number, log_index=r.log_index, ts=r.block_time, tx=tx,
                tot=tot, lot=lot, pay=pay, bought=bought, burn=b, hop=h, farm=fm, flapper=fl,
            ))
        leftover = {tx: v for tx, v in execs.items() if v}
        if leftover:
            raise ValueError(
                f"hypersync_sbe: {sum(len(v) for v in leftover.values())} Flapper Exec event(s) "
                f"with no matching Splitter Kick in tx(s) {sorted(leftover)[:3]} — the Flapper "
                "was called outside the Splitter; refusing to attribute the SKY"
            )
        return act
