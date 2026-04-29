"""Uniswap V3 position-pricer primitives.

Two layers:

1. **RPC reads** — NFT-position-manager + pool state via JSON-RPC (cached).
2. **Tick math** — pure Python translation of the canonical Uniswap V3
   ``TickMath.getSqrtRatioAtTick`` and ``LiquidityAmounts`` libraries. Bit-for-bit
   compatible with the Solidity reference: every constant is the precomputed
   Q128.128 fixed-point ratio that the on-chain library uses.

The NFPM contract is at the same canonical address on all V3 chains
(Ethereum / Base / Arbitrum / Optimism / etc):
``0xC36442b4a4522E871399CD717aBDD847Ab11FE88``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.primes import Address, Chain
from .cache import cached
from .rpc import RPCError, eth_call, eth_get_logs

# Canonical NonfungiblePositionManager address (same on all V3 EVM deployments).
NFPM_CANONICAL = Address.from_str("0xC36442b4a4522E871399CD717aBDD847Ab11FE88")

# Selectors
SEL_BALANCE_OF = "0x70a08231"               # balanceOf(address)
SEL_TOKEN_OF_OWNER_BY_INDEX = "0x2f745c59"  # tokenOfOwnerByIndex(address,uint256)
SEL_POSITIONS = "0x99fbab88"                # positions(uint256) → 12-tuple
SEL_SLOT0 = "0x3850c7bd"                    # slot0() → 7-tuple (sqrtPriceX96, tick, ...)
SEL_TOKEN0 = "0x0dfe1681"                   # token0() → address
SEL_TOKEN1 = "0xd21220a7"                   # token1() → address
SEL_FEE = "0xddca3f43"                      # fee() → uint24
SEL_FEE_GROWTH_GLOBAL_0 = "0xf3058399"      # feeGrowthGlobal0X128() → uint256
SEL_FEE_GROWTH_GLOBAL_1 = "0x46141319"      # feeGrowthGlobal1X128() → uint256
SEL_TICKS = "0xf30dba93"                    # ticks(int24) → 8-tuple

# NFPM event topics — keccak256 of the canonical signatures, per V3 reference.
TOPIC_INCREASE_LIQUIDITY = "0x3067048beee31b25b2f1681f88dac838c8bba36af25bfb2b7cf7473a5847e35f"
TOPIC_DECREASE_LIQUIDITY = "0x26f6a048ee9138f2c0ce266f322cb99228e8d619ae2bff30c67f8dcf9d2377b4"

# V3 tick range bounds (per TickMath.MIN/MAX_TICK)
MIN_TICK = -887272
MAX_TICK = 887272

# Solidity uint256 modulus — fee-growth subtractions wrap, so we mimic that.
_UINT256 = 1 << 256


from ._abi import (  # noqa: E402
    decode_address as _decode_address,
    pad_address as _pad_address,
    pad_uint as _pad_uint,
)


def _decode_int24(word: str) -> int:
    """Decode a sign-extended int24 stored in a 32-byte word."""
    raw = int(word.removeprefix("0x"), 16)
    if raw >= (1 << 255):
        raw -= 1 << 256
    return raw


# ----------------------------------------------------------------------------
# Tick math — exact translation of Uniswap V3 TickMath.sol
# ----------------------------------------------------------------------------

def get_sqrt_ratio_at_tick(tick: int) -> int:
    """Return ``sqrtPriceX96`` (Q64.96 fixed-point) for a given tick.

    Bit-for-bit equivalent of ``TickMath.getSqrtRatioAtTick`` from the V3
    reference implementation. For each bit set in ``|tick|``, multiply the
    accumulator by the corresponding precomputed Q128.128 ratio; final result
    is converted from Q128.128 to Q96.96 by right-shifting 32 bits with
    round-up if the truncated bits are non-zero.
    """
    if not (MIN_TICK <= tick <= MAX_TICK):
        raise ValueError(f"tick {tick} out of range [{MIN_TICK}, {MAX_TICK}]")

    abs_tick = abs(tick)
    ratio = 0xfffcb933bd6fad37aa2d162d1a594001 if (abs_tick & 0x1) else 0x100000000000000000000000000000000
    if abs_tick & 0x2:     ratio = (ratio * 0xfff97272373d413259a46990580e213a) >> 128
    if abs_tick & 0x4:     ratio = (ratio * 0xfff2e50f5f656932ef12357cf3c7fdcc) >> 128
    if abs_tick & 0x8:     ratio = (ratio * 0xffe5caca7e10e4e61c3624eaa0941cd0) >> 128
    if abs_tick & 0x10:    ratio = (ratio * 0xffcb9843d60f6159c9db58835c926644) >> 128
    if abs_tick & 0x20:    ratio = (ratio * 0xff973b41fa98c081472e6896dfb254c0) >> 128
    if abs_tick & 0x40:    ratio = (ratio * 0xff2ea16466c96a3843ec78b326b52861) >> 128
    if abs_tick & 0x80:    ratio = (ratio * 0xfe5dee046a99a2a811c461f1969c3053) >> 128
    if abs_tick & 0x100:   ratio = (ratio * 0xfcbe86c7900a88aedcffc83b479aa3a4) >> 128
    if abs_tick & 0x200:   ratio = (ratio * 0xf987a7253ac413176f2b074cf7815e54) >> 128
    if abs_tick & 0x400:   ratio = (ratio * 0xf3392b0822b70005940c7a398e4b70f3) >> 128
    if abs_tick & 0x800:   ratio = (ratio * 0xe7159475a2c29b7443b29c7fa6e889d9) >> 128
    if abs_tick & 0x1000:  ratio = (ratio * 0xd097f3bdfd2022b8845ad8f792aa5825) >> 128
    if abs_tick & 0x2000:  ratio = (ratio * 0xa9f746462d870fdf8a65dc1f90e061e5) >> 128
    if abs_tick & 0x4000:  ratio = (ratio * 0x70d869a156d2a1b890bb3df62baf32f7) >> 128
    if abs_tick & 0x8000:  ratio = (ratio * 0x31be135f97d08fd981231505542fcfa6) >> 128
    if abs_tick & 0x10000: ratio = (ratio * 0x9aa508b5b7a84e1c677de54f3e99bc9) >> 128
    if abs_tick & 0x20000: ratio = (ratio * 0x5d6af8dedb81196699c329225ee604) >> 128
    if abs_tick & 0x40000: ratio = (ratio * 0x2216e584f5fa1ea926041bedfe98) >> 128
    if abs_tick & 0x80000: ratio = (ratio * 0x48a170391f7dc42444e8fa2) >> 128

    if tick > 0:
        ratio = ((1 << 256) - 1) // ratio

    # Q128.128 → Q96.96 (round up).
    sqrt_price_x96 = (ratio >> 32) + (1 if (ratio & ((1 << 32) - 1)) != 0 else 0)
    return sqrt_price_x96


def _amount0_for_liquidity(sqrt_a: int, sqrt_b: int, liquidity: int) -> int:
    """`L × (sqrt_b - sqrt_a) × 2^96 / (sqrt_b × sqrt_a)`. Mirrors LiquidityAmounts."""
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    return ((liquidity * (sqrt_b - sqrt_a)) << 96) // (sqrt_b * sqrt_a) if sqrt_a > 0 else 0


def _amount1_for_liquidity(sqrt_a: int, sqrt_b: int, liquidity: int) -> int:
    """`L × (sqrt_b - sqrt_a) / 2^96`."""
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    return (liquidity * (sqrt_b - sqrt_a)) >> 96


def get_amounts_for_liquidity(
    sqrt_price_x96: int,
    sqrt_a_x96: int,
    sqrt_b_x96: int,
    liquidity: int,
) -> tuple[int, int]:
    """``(amount0, amount1)`` for a position with the given liquidity range.

    Three-way branch on the current price:
    - Below range (sqrt ≤ sqrt_a): all liquidity in token0
    - In range (sqrt_a < sqrt < sqrt_b): split
    - At/above range (sqrt ≥ sqrt_b): all liquidity in token1
    """
    if sqrt_a_x96 > sqrt_b_x96:
        sqrt_a_x96, sqrt_b_x96 = sqrt_b_x96, sqrt_a_x96

    if sqrt_price_x96 <= sqrt_a_x96:
        return _amount0_for_liquidity(sqrt_a_x96, sqrt_b_x96, liquidity), 0
    if sqrt_price_x96 < sqrt_b_x96:
        amount0 = _amount0_for_liquidity(sqrt_price_x96, sqrt_b_x96, liquidity)
        amount1 = _amount1_for_liquidity(sqrt_a_x96, sqrt_price_x96, liquidity)
        return amount0, amount1
    return 0, _amount1_for_liquidity(sqrt_a_x96, sqrt_b_x96, liquidity)


# ----------------------------------------------------------------------------
# RPC readers — NFPM + pool
# ----------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class V3PoolState:
    sqrt_price_x96: int
    current_tick: int
    token0: Address
    token1: Address
    fee: int
    fee_growth_global_0_x128: int
    fee_growth_global_1_x128: int


@dataclass(frozen=True, slots=True)
class V3Position:
    """One V3 position as returned by ``NFPM.positions(tokenId)``. Raw — no
    amount math yet.

    ``fee_growth_inside_{0,1}_last_x128`` are the feeGrowthInside snapshots
    captured at the position's last on-chain interaction
    (mint / increaseLiquidity / decreaseLiquidity / collect). Pending fees
    accrued *since* that snapshot are computed by ``compute_pending_fees``.
    """
    token_id: int
    token0: Address
    token1: Address
    fee: int
    tick_lower: int
    tick_upper: int
    liquidity: int
    fee_growth_inside_0_last_x128: int
    fee_growth_inside_1_last_x128: int
    tokens_owed_0: int
    tokens_owed_1: int


@dataclass(frozen=True, slots=True)
class V3TickInfo:
    """Subset of ``Pool.ticks(tick)`` we need for fee math."""
    fee_growth_outside_0_x128: int
    fee_growth_outside_1_x128: int


@cached(source_id="uniswap_v3.balance_of_nfpm")
def nfpm_balance_of(chain: Chain, nfpm: Address, owner: Address, block: int) -> int:
    """Number of V3 NFTs the owner holds at ``block``."""
    data = SEL_BALANCE_OF + _pad_address(owner)
    return int(eth_call(chain, nfpm, data, block), 16)


@cached(source_id="uniswap_v3.token_of_owner_by_index")
def token_of_owner_by_index(
    chain: Chain, nfpm: Address, owner: Address, index: int, block: int,
) -> int:
    """Returns the i-th tokenId owned by ``owner`` (ERC-721 enumerable extension)."""
    data = SEL_TOKEN_OF_OWNER_BY_INDEX + _pad_address(owner) + _pad_uint(index)
    return int(eth_call(chain, nfpm, data, block), 16)


@cached(source_id="uniswap_v3.positions")
def read_position(chain: Chain, nfpm: Address, token_id: int, block: int) -> V3Position:
    """``NFPM.positions(tokenId)`` decoder.

    Returns 12-tuple: (nonce, operator, token0, token1, fee, tickLower, tickUpper,
    liquidity, feeGrowthInside0LastX128, feeGrowthInside1LastX128, tokensOwed0, tokensOwed1).
    Each field is one 32-byte word, decoded by offset.
    """
    data = SEL_POSITIONS + _pad_uint(token_id)
    raw = eth_call(chain, nfpm, data, block).removeprefix("0x")
    # Each 32-byte word = 64 hex chars
    def word(i: int) -> str:
        return raw[i * 64 : (i + 1) * 64]

    return V3Position(
        token_id=token_id,
        token0=_decode_address("0x" + word(2)),
        token1=_decode_address("0x" + word(3)),
        fee=int(word(4), 16),
        tick_lower=_decode_int24("0x" + word(5)),
        tick_upper=_decode_int24("0x" + word(6)),
        liquidity=int(word(7), 16),
        fee_growth_inside_0_last_x128=int(word(8), 16),
        fee_growth_inside_1_last_x128=int(word(9), 16),
        tokens_owed_0=int(word(10), 16),
        tokens_owed_1=int(word(11), 16),
    )


@cached(source_id="uniswap_v3.slot0")
def read_pool_state(chain: Chain, pool: Address, block: int) -> V3PoolState:
    """Read pool's current sqrtPriceX96, tick, token0/1, fee, and global fee
    growth accumulators (needed to derive pending position fees)."""
    raw_slot0 = eth_call(chain, pool, SEL_SLOT0, block).removeprefix("0x")
    sqrt_price_x96 = int(raw_slot0[:64], 16) & ((1 << 160) - 1)
    current_tick = _decode_int24("0x" + raw_slot0[64:128])

    token0 = _decode_address(eth_call(chain, pool, SEL_TOKEN0, block))
    token1 = _decode_address(eth_call(chain, pool, SEL_TOKEN1, block))
    fee = int(eth_call(chain, pool, SEL_FEE, block), 16)
    fg0 = int(eth_call(chain, pool, SEL_FEE_GROWTH_GLOBAL_0, block), 16)
    fg1 = int(eth_call(chain, pool, SEL_FEE_GROWTH_GLOBAL_1, block), 16)

    return V3PoolState(
        sqrt_price_x96=sqrt_price_x96,
        current_tick=current_tick,
        token0=token0,
        token1=token1,
        fee=fee,
        fee_growth_global_0_x128=fg0,
        fee_growth_global_1_x128=fg1,
    )


@cached(source_id="uniswap_v3.ticks")
def read_tick(chain: Chain, pool: Address, tick: int, block: int) -> V3TickInfo:
    """Read ``Pool.ticks(tick)``. Returns 8-tuple; we only decode the two
    feeGrowthOutside accumulators."""
    if not (MIN_TICK <= tick <= MAX_TICK):
        raise ValueError(f"tick {tick} out of range [{MIN_TICK}, {MAX_TICK}]")
    # Encode int24 as 32-byte two's complement.
    tick_word = tick & ((1 << 256) - 1) if tick >= 0 else (tick + (1 << 256))
    data = SEL_TICKS + _pad_uint(tick_word)
    raw = eth_call(chain, pool, data, block).removeprefix("0x")
    # Words: 0=liquidityGross, 1=liquidityNet, 2=feeGrowthOutside0, 3=feeGrowthOutside1, ...
    return V3TickInfo(
        fee_growth_outside_0_x128=int(raw[2 * 64:3 * 64], 16),
        fee_growth_outside_1_x128=int(raw[3 * 64:4 * 64], 16),
    )


def compute_pending_fees(
    *,
    current_tick: int,
    tick_lower: int,
    tick_upper: int,
    fee_growth_global_0_x128: int,
    fee_growth_global_1_x128: int,
    lower_outside_0_x128: int,
    lower_outside_1_x128: int,
    upper_outside_0_x128: int,
    upper_outside_1_x128: int,
    fee_growth_inside_0_last_x128: int,
    fee_growth_inside_1_last_x128: int,
    liquidity: int,
) -> tuple[int, int]:
    """Pending (uncollected, unmaterialized) fees for one V3 position.

    Mirrors Solidity ``Pool._getFeeGrowthInside`` + the position's pending-fee
    formula. All subtractions are uint256 modular — Solidity exploits this so
    a wrap during initialization doesn't corrupt the running delta.

    Reference: ``v3-core/contracts/UniswapV3Pool.sol::_getFeeGrowthInside`` and
    ``v3-core/contracts/libraries/Position.sol::update``.
    """
    def fee_growth_inside(global_x: int, lower_outside: int, upper_outside: int) -> int:
        below_x = lower_outside if current_tick >= tick_lower else (
            (global_x - lower_outside) % _UINT256
        )
        above_x = upper_outside if current_tick < tick_upper else (
            (global_x - upper_outside) % _UINT256
        )
        return (global_x - below_x - above_x) % _UINT256

    inside_0 = fee_growth_inside(
        fee_growth_global_0_x128, lower_outside_0_x128, upper_outside_0_x128,
    )
    inside_1 = fee_growth_inside(
        fee_growth_global_1_x128, lower_outside_1_x128, upper_outside_1_x128,
    )
    delta_0 = (inside_0 - fee_growth_inside_0_last_x128) % _UINT256
    delta_1 = (inside_1 - fee_growth_inside_1_last_x128) % _UINT256
    pending_0 = (delta_0 * liquidity) >> 128
    pending_1 = (delta_1 * liquidity) >> 128
    return pending_0, pending_1


# ----------------------------------------------------------------------------
# Liquidity events — Increase/DecreaseLiquidity from NFPM
# ----------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class V3LiquidityEvent:
    """One ``IncreaseLiquidity`` or ``DecreaseLiquidity`` event.

    Amounts are signed: ``+`` on increase (deposit), ``-`` on decrease
    (withdrawal). This makes the inflow timeseries trivially summable —
    revenue = Δvalue − Σ signed_inflow.
    """
    block_number: int
    tx_hash: str
    log_index: int
    token_id: int
    amount0: int
    amount1: int
    is_increase: bool


def _decode_liquidity_log(log: dict) -> V3LiquidityEvent:
    """Decode an NFPM ``IncreaseLiquidity`` / ``DecreaseLiquidity`` log.

    Indexed: ``tokenId`` is in ``topics[1]``.
    Data:    32-byte words for ``liquidity`` (uint128 padded), ``amount0``,
             ``amount1`` (uint256 each). We skip ``liquidity`` because the
             Q96.96 amounts are sufficient to reconstruct USD value.
    """
    topics = log["topics"]
    is_increase = topics[0].lower() == TOPIC_INCREASE_LIQUIDITY
    token_id = int(topics[1], 16)
    data = log["data"].removeprefix("0x")
    # data words: 0=liquidity (uint128 in 32-byte word), 1=amount0, 2=amount1
    amount0 = int(data[64:128], 16)
    amount1 = int(data[128:192], 16)
    sign = 1 if is_increase else -1
    return V3LiquidityEvent(
        block_number=int(log["blockNumber"], 16),
        tx_hash=log["transactionHash"],
        log_index=int(log["logIndex"], 16),
        token_id=token_id,
        amount0=sign * amount0,
        amount1=sign * amount1,
        is_increase=is_increase,
    )


def read_liquidity_events(
    chain: Chain,
    nfpm: Address,
    token_id: int,
    from_block: int,
    to_block: int,
) -> list[V3LiquidityEvent]:
    """All ``Increase`` + ``Decrease`` liquidity events for ``token_id`` in
    ``(from_block, to_block]``.

    Uses ``eth_getLogs`` with topic[0] in {Increase, Decrease} and
    topic[1] = padded(tokenId). Two queries (one per topic[0] value).
    Pagination is handled inside ``eth_get_logs``.
    """
    if from_block > to_block:
        return []
    token_id_topic = "0x" + _pad_uint(token_id)
    out: list[V3LiquidityEvent] = []
    for topic0 in (TOPIC_INCREASE_LIQUIDITY, TOPIC_DECREASE_LIQUIDITY):
        logs = eth_get_logs(
            chain, nfpm,
            topics=[topic0, token_id_topic],
            from_block=from_block, to_block=to_block,
        )
        out.extend(_decode_liquidity_log(log) for log in logs)
    out.sort(key=lambda e: (e.block_number, e.log_index))
    return out
