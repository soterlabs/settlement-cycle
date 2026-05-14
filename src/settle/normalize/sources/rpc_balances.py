"""RPC-backed ``IBalanceSource`` for chains not in Dune's spellbook.

Scans ERC-20 ``Transfer`` events via ``eth_getLogs`` (paginated + cached)
to produce the same three timeseries shapes ``DuneBalanceSource``
publishes — daily cumulative balance, directed inflow per day, and
per-counterparty signed flow per day. Decimal-adjusts via
``rpc.decimals_of(token)`` so downstream math is in human units, matching
Dune's ``tokens.transfers.amount`` convention.

Block ↔ date conversion is delegated to the injected ``block_resolver``
(typically ``RPCBlockResolver`` for non-Dune chains). One ``block_to_date``
call per event; ``rpc.block_timestamp`` is cached so repeated calls at
the same block are free.

Cost shape: a single ~12-month scan on Monad ≈ 30M blocks at 10K-block
chunks = ~3000 ``eth_getLogs`` calls. Cached on first run; subsequent
periods only re-fetch the new tail. Decode work is bounded by event
count (Monad's bbqAUSD vault has dozens of events over a year, not
millions).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal

import pandas as pd

from ...domain.primes import Address, Chain
from ...extract import rpc
from ...extract.transfer_logs import scan_transfers


class RPCBalanceSource:
    """``IBalanceSource`` driven entirely by JSON-RPC ``eth_getLogs``.

    ``block_resolver`` is required to convert dates ↔ block numbers; pass
    an ``RPCBlockResolver`` for chains not in Dune coverage. Each method
    reads ``rpc.decimals_of(token, pin_block)`` once per call (cached)
    to convert raw uint256 amounts to ``Decimal``.
    """

    def __init__(self, block_resolver) -> None:
        self._br = block_resolver

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_block(self, chain: str, start: date, pin_block: int) -> int:
        """Resolve the first block at-or-after ``start`` (UTC midnight).

        ``block_at_or_before`` returns the highest block ≤ anchor; we want
        the LOWEST block on or after, so we add 1 — except when that would
        exceed ``pin_block`` (empty period).
        """
        anchor = datetime.combine(start, time.min, tzinfo=timezone.utc)
        b = self._br.block_at_or_before(chain, anchor)
        return min(b + 1, pin_block)

    def _block_date(self, chain: str, block_number: int) -> date:
        return self._br.block_to_date(chain, block_number)

    def _decimals(self, chain: str, token: bytes, pin_block: int) -> int:
        return rpc.decimals_of(Chain(chain), Address(token), pin_block)

    # ------------------------------------------------------------------
    # IBalanceSource methods
    # ------------------------------------------------------------------

    def cumulative_balance_timeseries(
        self,
        chain: str,
        token: bytes,
        holder: bytes,
        start: date,
        pin_block: int,
        min_transfer_amount: Decimal = Decimal(0),
    ) -> pd.DataFrame:
        start_block = self._start_block(chain, start, pin_block)
        if start_block > pin_block:
            return pd.DataFrame({
                "block_date": [], "daily_net": [], "cum_balance": [],
            })

        # Two scans: one for inflows (to=holder), one for outflows (from=holder).
        # ``eth_getLogs`` doesn't support OR on topics, so we union in Python.
        in_logs = scan_transfers(
            chain, token, start_block, pin_block, to_filter=holder,
        )
        out_logs = scan_transfers(
            chain, token, start_block, pin_block, from_filter=holder,
        )

        scale = Decimal(10 ** self._decimals(chain, token, pin_block))
        threshold = Decimal(min_transfer_amount or 0)

        daily: dict[date, Decimal] = defaultdict(lambda: Decimal(0))
        for block_number, from_addr, to_addr, value_raw in in_logs:
            # Self-transfers (from == to == holder) cancel: skip on the in-pass
            # so they only contribute zero net on the out-pass.
            if from_addr == holder:
                continue
            amount = Decimal(value_raw) / scale
            if amount < threshold:
                continue
            daily[self._block_date(chain, block_number)] += amount
        for block_number, from_addr, to_addr, value_raw in out_logs:
            amount = Decimal(value_raw) / scale
            if amount < threshold:
                continue
            daily[self._block_date(chain, block_number)] -= amount

        if not daily:
            return pd.DataFrame({
                "block_date": [], "daily_net": [], "cum_balance": [],
            })

        sorted_dates = sorted(daily)
        daily_net = [daily[d] for d in sorted_dates]
        cum: list[Decimal] = []
        running = Decimal(0)
        for v in daily_net:
            running += v
            cum.append(running)
        return pd.DataFrame({
            "block_date":  sorted_dates,
            "daily_net":   daily_net,
            "cum_balance": cum,
        })

    def directed_inflow_timeseries(
        self,
        chain: str,
        token: bytes,
        from_addr: bytes,
        to_addr: bytes,
        start: date,
        pin_block: int,
    ) -> pd.DataFrame:
        start_block = self._start_block(chain, start, pin_block)
        if start_block > pin_block:
            return pd.DataFrame({
                "block_date": [], "daily_inflow": [], "cum_inflow": [],
            })
        logs = scan_transfers(
            chain, token, start_block, pin_block,
            from_filter=from_addr, to_filter=to_addr,
        )
        scale = Decimal(10 ** self._decimals(chain, token, pin_block))
        daily: dict[date, Decimal] = defaultdict(lambda: Decimal(0))
        for block_number, _f, _t, value_raw in logs:
            daily[self._block_date(chain, block_number)] += Decimal(value_raw) / scale
        if not daily:
            return pd.DataFrame({
                "block_date": [], "daily_inflow": [], "cum_inflow": [],
            })
        sorted_dates = sorted(daily)
        daily_inflow = [daily[d] for d in sorted_dates]
        cum: list[Decimal] = []
        running = Decimal(0)
        for v in daily_inflow:
            running += v
            cum.append(running)
        return pd.DataFrame({
            "block_date":   sorted_dates,
            "daily_inflow": daily_inflow,
            "cum_inflow":   cum,
        })

    def inflow_by_counterparty(
        self,
        chain: str,
        token: bytes,
        holder: bytes,
        start: date,
        pin_block: int,
    ) -> pd.DataFrame:
        start_block = self._start_block(chain, start, pin_block)
        if start_block > pin_block:
            return pd.DataFrame({
                "block_date": [], "counterparty": [], "signed_amount": [],
            })

        in_logs = scan_transfers(
            chain, token, start_block, pin_block, to_filter=holder,
        )
        out_logs = scan_transfers(
            chain, token, start_block, pin_block, from_filter=holder,
        )
        scale = Decimal(10 ** self._decimals(chain, token, pin_block))

        # Aggregate per (block_date, counterparty). Self-transfers (holder
        # on both sides) net to zero by appearing once with +amount and
        # once with -amount under the same counterparty=holder key.
        pair: dict[tuple[date, bytes], Decimal] = defaultdict(lambda: Decimal(0))
        for block_number, from_addr, _to, value_raw in in_logs:
            d = self._block_date(chain, block_number)
            amount = Decimal(value_raw) / scale
            pair[(d, from_addr)] += amount
        for block_number, _from, to_addr, value_raw in out_logs:
            d = self._block_date(chain, block_number)
            amount = Decimal(value_raw) / scale
            pair[(d, to_addr)] -= amount

        if not pair:
            return pd.DataFrame({
                "block_date": [], "counterparty": [], "signed_amount": [],
            })

        rows = sorted(pair.items(), key=lambda kv: (kv[0][0], kv[0][1]))
        return pd.DataFrame({
            "block_date":    [d for (d, _cp), _ in rows],
            "counterparty":  [cp for (_d, cp), _ in rows],
            "signed_amount": [v for _, v in rows],
        })
