"""Per-chain balance-source dispatcher.

Mirrors :class:`~settle.normalize.sources.dune_block_resolver.MultiChainBlockResolver`
for the ``IBalanceSource`` shape. Every method takes a ``chain`` argument
already, so dispatch is a one-line lookup before delegating to the
backend registered for that chain.

Built by ``compute_monthly_pnl`` when a prime spans Dune-indexed chains
(ethereum, base, arbitrum, optimism, avalanche_c) AND chains that need
RPC fallback (monad, unichain, plume). The Dune-coverage subset is
identified by ``_DUNE_BLOCK_CHAINS`` upstream.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from ..protocols import IBalanceSource


class MultiChainBalanceSource:
    """Wraps per-chain ``IBalanceSource`` backends; dispatches by ``chain``.

    Each backend is queried only for the chain it owns — Dune calls never
    hit a Monad RPC, and the eth_getLogs path never queries an Ethereum
    venue. No fallback between backends: if a chain isn't registered the
    dispatcher raises so the operator sees the gap.
    """

    def __init__(self, per_chain: dict[str, IBalanceSource]) -> None:
        # dict[str, IBalanceSource] keyed by Chain.value (e.g. 'ethereum').
        self._per_chain = dict(per_chain)

    def _get(self, chain: str) -> IBalanceSource:
        try:
            return self._per_chain[chain]
        except KeyError:
            raise ValueError(
                f"MultiChainBalanceSource: no backend registered for {chain!r}. "
                f"Have: {sorted(self._per_chain)}"
            ) from None

    def cumulative_balance_timeseries(
        self,
        chain: str,
        token: bytes,
        holder: bytes,
        start: date,
        pin_block: int,
        min_transfer_amount: Decimal = Decimal(0),
    ) -> pd.DataFrame:
        return self._get(chain).cumulative_balance_timeseries(
            chain, token, holder, start, pin_block, min_transfer_amount,
        )

    def directed_inflow_timeseries(
        self,
        chain: str,
        token: bytes,
        from_addr: bytes,
        to_addr: bytes,
        start: date,
        pin_block: int,
    ) -> pd.DataFrame:
        return self._get(chain).directed_inflow_timeseries(
            chain, token, from_addr, to_addr, start, pin_block,
        )

    def inflow_by_counterparty(
        self,
        chain: str,
        token: bytes,
        holder: bytes,
        start: date,
        pin_block: int,
    ) -> pd.DataFrame:
        return self._get(chain).inflow_by_counterparty(
            chain, token, holder, start, pin_block,
        )
