"""Curve pool reader Source — pulls all pool state needed to price an LP.

Wraps `extract.curve` into one call that returns everything the price function
needs, so the math layer doesn't fan out RPC calls itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...domain.primes import Address, Chain
from ...extract import curve as curve_rpc


@dataclass(frozen=True, slots=True)
class CurvePoolState:
    """Pool snapshot at a specific block."""

    virtual_price_raw: int    # 1e18-scaled
    total_supply: int         # raw, divide by LP decimals
    coins: list[Address]      # underlying token addresses, in pool order
    balances: list[int]       # raw reserves, in pool order


class CurvePoolSource:
    """Reads Curve stableswap pool state at a block via JSON-RPC, plus tracks
    AddLiquidity / RemoveLiquidity[*] events for a given provider over a
    block range (used for per-venue inflow accounting)."""

    def read_pool(self, chain: str, pool_address: bytes, block: int) -> CurvePoolState:
        chain_e = Chain(chain)
        pool = Address(pool_address)

        n = curve_rpc.n_coins(chain_e, pool, block)
        coins = [curve_rpc.coin_at(chain_e, pool, i, block) for i in range(n)]
        balances = [curve_rpc.balance_at(chain_e, pool, i, block) for i in range(n)]

        return CurvePoolState(
            virtual_price_raw=curve_rpc.get_virtual_price(chain_e, pool, block),
            total_supply=curve_rpc.total_supply(chain_e, pool, block),
            coins=coins,
            balances=balances,
        )

    def liquidity_events_for_provider(
        self,
        chain: str,
        pool_address: bytes,
        provider: bytes,
        from_block: int,
        to_block: int,
    ) -> list[curve_rpc.CurveLiquidityEvent]:
        """All Add/Remove events emitted by ``pool_address`` with the indexed
        ``provider``, between (from_block, to_block]. Signed amounts net out
        rebalancing in the period_inflow timeseries.

        ⚠ Same Alchemy free-tier rate-limit caveat as the V3 inflow path —
        production runs need a Dune-backed implementation."""
        return curve_rpc.read_liquidity_events(
            Chain(chain),
            Address(pool_address),
            Address(provider),
            from_block + 1, to_block,
        )
