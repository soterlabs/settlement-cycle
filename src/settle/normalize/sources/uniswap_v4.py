"""Uniswap V4 position source.

Reads the v4 LP positions a holder owns (token ids supplied by config — v4's
PositionManager isn't enumerable), filters to a target pool, and computes each
position's ``(amount0, amount1)`` via the shared V3 tick math. The
``normalize.prices``/``positions`` layer then converts amounts to USD (par for
the stable pairs in scope).

Two reads:

* ``positions_in_pool`` — snapshot holdings (principal at the current price).
  Uncollected LP fees are intentionally NOT added: the pools in scope are
  tight stable ranges (USDS vs a par-stable) whose monthly fee accrual is a
  few bps of a par position — immaterial to settlement — and v4 fee-growth
  reads require substantially more ``extsload`` slot math. Documented
  approximation; promote to full fee accounting if a non-stable v4 pool
  appears.
* ``liquidity_flows_in_pool`` — signed capital flow per ``ModifyLiquidity``
  event, converting ``liquidityDelta`` to token amounts at the event block's
  price (mirrors the V3 inflow path so revenue = Δvalue − Σ inflow holds even
  when positions are minted/burned mid-period).
"""

from __future__ import annotations

from dataclasses import dataclass

from ...domain.primes import Address, Chain
from ...extract import uniswap_v3 as v3
from ...extract import uniswap_v4 as v4


@dataclass(frozen=True, slots=True)
class V4PositionAmounts:
    token_id: int
    currency0: Address
    currency1: Address
    amount0: int
    amount1: int


@dataclass(frozen=True, slots=True)
class V4LiquidityFlow:
    """Signed token amounts for one ``ModifyLiquidity`` event (+ add / − remove)."""

    block_number: int
    token_id: int
    amount0: int
    amount1: int


class RPCUniswapV4PositionSource:
    """Reads v4 NFT positions for an owner in a target pool over JSON-RPC.

    ``position_manager`` / ``pool_manager`` default to the canonical Ethereum
    singletons; override per-chain for other deployments.
    """

    def __init__(
        self,
        position_manager_per_chain: dict[Chain, Address] | None = None,
        pool_manager_per_chain: dict[Chain, Address] | None = None,
    ) -> None:
        self._pm_overrides = position_manager_per_chain or {}
        self._poolmgr_overrides = pool_manager_per_chain or {}

    def _position_manager(self, chain: Chain) -> Address:
        return self._pm_overrides.get(chain, v4.POSITION_MANAGER_CANONICAL)

    def _pool_manager(self, chain: Chain) -> Address:
        return self._poolmgr_overrides.get(chain, v4.POOL_MANAGER_CANONICAL)

    def positions_in_pool(
        self,
        chain: str,
        owner: bytes,
        token_ids: list[int],
        pool_key: v4.V4PoolKey,
        block: int,
    ) -> list[V4PositionAmounts]:
        """Holdings for each owned ``token_id`` in ``pool_key`` at ``block``.

        Skips token ids that aren't minted, aren't owned by ``owner``, or
        belong to another pool. Returns ``[]`` if the pool is uninitialized at
        ``block`` (pre-deployment snapshot).
        """
        chain_e = Chain(chain)
        owner_a = Address(owner)
        pm = self._position_manager(chain_e)
        poolmgr = self._pool_manager(chain_e)
        pool_id = pool_key.pool_id()

        slot0 = v4.read_slot0(chain_e, poolmgr, pool_id, block)
        if slot0 is None:
            return []

        out: list[V4PositionAmounts] = []
        for tid in token_ids:
            pos = v4.read_position(
                chain_e, pm, tid, block,
                holder=owner_a, expected_pool_id=pool_id,
            )
            if pos is None:
                continue
            sqrt_a = v3.get_sqrt_ratio_at_tick(pos.tick_lower)
            sqrt_b = v3.get_sqrt_ratio_at_tick(pos.tick_upper)
            amount0, amount1 = v3.get_amounts_for_liquidity(
                slot0.sqrt_price_x96, sqrt_a, sqrt_b, pos.liquidity,
            )
            out.append(V4PositionAmounts(
                token_id=tid,
                currency0=pool_key.currency0,
                currency1=pool_key.currency1,
                amount0=amount0,
                amount1=amount1,
            ))
        return out

    def liquidity_flows_in_pool(
        self,
        chain: str,
        token_ids: list[int],
        pool_key: v4.V4PoolKey,
        from_block: int,
        to_block: int,
    ) -> list[V4LiquidityFlow]:
        """Signed capital flows from ``ModifyLiquidity`` events for the venue's
        token ids in ``(from_block, to_block]``.

        Each event's ``liquidityDelta`` is converted to token amounts at that
        block's pool price, so the resulting flows sum to the net capital
        added/removed over the period.
        """
        chain_e = Chain(chain)
        poolmgr = self._pool_manager(chain_e)
        pool_id = pool_key.pool_id()
        token_id_set = set(token_ids)

        # Scope events to our PositionManager (the on-chain position ``owner`` /
        # event ``sender``). ``salt`` (= token id) is only unique per owner, so
        # without this a colliding salt from another LP in the same pool would
        # be picked up by the ``token_id_set`` filter below. Uses the same
        # PositionManager the value path (``positions_in_pool``) resolves, so
        # the two paths stay consistent by construction.
        events = v4.read_modify_liquidity_events(
            chain_e, poolmgr, pool_id, from_block + 1, to_block,
            sender=self._position_manager(chain_e),
        )
        out: list[V4LiquidityFlow] = []
        for ev in events:
            if ev.token_id not in token_id_set:
                continue
            slot0 = v4.read_slot0(chain_e, poolmgr, pool_id, ev.block_number)
            if slot0 is None:
                continue
            sqrt_a = v3.get_sqrt_ratio_at_tick(ev.tick_lower)
            sqrt_b = v3.get_sqrt_ratio_at_tick(ev.tick_upper)
            mag0, mag1 = v3.get_amounts_for_liquidity(
                slot0.sqrt_price_x96, sqrt_a, sqrt_b, abs(ev.liquidity_delta),
            )
            sign = 1 if ev.liquidity_delta > 0 else -1
            out.append(V4LiquidityFlow(
                block_number=ev.block_number,
                token_id=ev.token_id,
                amount0=sign * mag0,
                amount1=sign * mag1,
            ))
        return out
