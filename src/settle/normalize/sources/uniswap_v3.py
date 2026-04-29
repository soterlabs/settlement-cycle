"""Uniswap V3 position source.

Walks all NFPM-held NFTs for a given owner, filters to the target pool, computes
``(amount0, amount1)`` per position via the V3 tick math, and adds the full
fee balance: materialized ``tokensOwed0/1`` *plus* fees that have accrued
since the position's last on-chain interaction (derived from the pool's
``feeGrowthInside`` accumulators). The math layer in ``normalize.prices`` then
converts each amount to USD.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...domain.primes import Address, Chain
from ...extract import uniswap_v3 as v3


@dataclass(frozen=True, slots=True)
class V3PositionAmounts:
    """One V3 position's redeemable holdings at a snapshot block.

    ``amount0`` / ``amount1`` are the raw token amounts (in token units, not
    USD): sum of (a) liquidity-implied amounts at the current sqrtPriceX96,
    (b) materialized fees in ``tokensOwed0/1``, and (c) pending fees derived
    from the pool's ``feeGrowthInside`` deltas since the position's last
    on-chain interaction.
    """
    token_id: int
    token0: Address
    token1: Address
    amount0: int
    amount1: int


class RPCUniswapV3PositionSource:
    """Reads V3 NFT positions for an owner in a target pool.

    Uses the canonical NFPM (`v3.NFPM_CANONICAL`); override per-chain via
    ``nfpm_per_chain`` if a deployment uses a non-canonical address.
    """

    def __init__(self, nfpm_per_chain: dict[Chain, Address] | None = None) -> None:
        self._nfpm_overrides = nfpm_per_chain or {}

    def _nfpm(self, chain: Chain) -> Address:
        return self._nfpm_overrides.get(chain, v3.NFPM_CANONICAL)

    def liquidity_events_in_pool(
        self,
        chain: str,
        owner: bytes,
        pool: bytes,
        from_block: int,
        to_block: int,
    ) -> list[v3.V3LiquidityEvent]:
        """All ``Increase``/``Decrease`` liquidity events from positions the
        owner holds in this pool, between ``from_block+1`` and ``to_block``.

        Walks unique tokenIds present at either boundary (covers positions
        open at SoM, EoM, or both) and pulls their NFPM events in the range.

        ⚠ This path uses ``eth_getLogs`` and is rate-limited by the RPC
        provider. Alchemy free tier caps each request at 10,000 blocks
        (see ``LOGS_CHUNK_BLOCKS`` in ``extract.rpc``); a busy pool that
        emits more than 10k logs in a single chunk will still hit the
        per-response log cap. For production runs, override this source
        with a Dune-backed implementation that scans ``ethereum.logs`` directly. Positions opened *and* closed
        entirely within the period are invisible to the boundary snapshots
        (no current Grove venue exhibits this pattern).
        """
        chain_e = Chain(chain)
        owner_a = Address(owner)
        pool_a = Address(pool)
        nfpm = self._nfpm(chain_e)

        pool_state = v3.read_pool_state(chain_e, pool_a, to_block)
        pool_target = (pool_state.token0.value, pool_state.token1.value, pool_state.fee)

        token_ids: set[int] = set()
        for boundary in (from_block, to_block):
            n = v3.nfpm_balance_of(chain_e, nfpm, owner_a, boundary)
            for i in range(n):
                tid = v3.token_of_owner_by_index(chain_e, nfpm, owner_a, i, boundary)
                pos = v3.read_position(chain_e, nfpm, tid, boundary)
                if (pos.token0.value, pos.token1.value, pos.fee) == pool_target:
                    token_ids.add(tid)

        out: list[v3.V3LiquidityEvent] = []
        for tid in sorted(token_ids):
            out.extend(v3.read_liquidity_events(
                chain_e, nfpm, tid, from_block + 1, to_block,
            ))
        out.sort(key=lambda e: (e.block_number, e.log_index))
        return out

    def positions_in_pool(
        self,
        chain: str,
        owner: bytes,
        pool: bytes,
        block: int,
    ) -> list[V3PositionAmounts]:
        """All NFPM-held positions whose (token0, token1, fee) match the pool.

        Returns empty list if the owner has zero positions, or if none match.
        """
        chain_e = Chain(chain)
        owner_a = Address(owner)
        pool_a = Address(pool)
        nfpm = self._nfpm(chain_e)

        # Pool target identity (token0, token1, fee).
        pool_state = v3.read_pool_state(chain_e, pool_a, block)
        pool_target = (pool_state.token0.value, pool_state.token1.value, pool_state.fee)

        n_positions = v3.nfpm_balance_of(chain_e, nfpm, owner_a, block)
        out: list[V3PositionAmounts] = []
        for i in range(n_positions):
            token_id = v3.token_of_owner_by_index(chain_e, nfpm, owner_a, i, block)
            pos = v3.read_position(chain_e, nfpm, token_id, block)

            # Filter to positions matching this pool.
            if (pos.token0.value, pos.token1.value, pos.fee) != pool_target:
                continue

            sqrt_a = v3.get_sqrt_ratio_at_tick(pos.tick_lower)
            sqrt_b = v3.get_sqrt_ratio_at_tick(pos.tick_upper)
            amount0, amount1 = v3.get_amounts_for_liquidity(
                pool_state.sqrt_price_x96, sqrt_a, sqrt_b, pos.liquidity,
            )
            # Materialized fees (snapshot at last interaction).
            amount0 += pos.tokens_owed_0
            amount1 += pos.tokens_owed_1
            # Pending fees (accrued since last interaction) — only when the
            # position holds liquidity. With L=0 the formula collapses to 0
            # anyway, but skipping saves two RPC reads per position.
            if pos.liquidity > 0:
                lower = v3.read_tick(chain_e, pool_a, pos.tick_lower, block)
                upper = v3.read_tick(chain_e, pool_a, pos.tick_upper, block)
                pending_0, pending_1 = v3.compute_pending_fees(
                    current_tick=pool_state.current_tick,
                    tick_lower=pos.tick_lower,
                    tick_upper=pos.tick_upper,
                    fee_growth_global_0_x128=pool_state.fee_growth_global_0_x128,
                    fee_growth_global_1_x128=pool_state.fee_growth_global_1_x128,
                    lower_outside_0_x128=lower.fee_growth_outside_0_x128,
                    lower_outside_1_x128=lower.fee_growth_outside_1_x128,
                    upper_outside_0_x128=upper.fee_growth_outside_0_x128,
                    upper_outside_1_x128=upper.fee_growth_outside_1_x128,
                    fee_growth_inside_0_last_x128=pos.fee_growth_inside_0_last_x128,
                    fee_growth_inside_1_last_x128=pos.fee_growth_inside_1_last_x128,
                    liquidity=pos.liquidity,
                )
                amount0 += pending_0
                amount1 += pending_1

            out.append(V3PositionAmounts(
                token_id=token_id,
                token0=pos.token0,
                token1=pos.token1,
                amount0=amount0,
                amount1=amount1,
            ))
        return out
