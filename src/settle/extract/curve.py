"""Curve stableswap pool reader.

Old-style Curve pools (the AUSDUSDC pool E11 is one) use a *mixed* signature:
- `coins(int128)`     not  `coins(uint256)`
- `balances(uint256)` not  `balances(int128)`

This reader probes both signatures lazily and caches per (chain, pool, block).
Newer Curve pools use uniform `uint256` signatures — handled by falling through
to the alternative selector if the first one reverts.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.primes import Address, Chain
from .cache import cached
from .rpc import RPCError, eth_call, eth_get_logs

# Pool selectors
SEL_GET_VIRTUAL_PRICE = "0xbb7b8b80"   # get_virtual_price()
SEL_TOTAL_SUPPLY      = "0x18160ddd"   # totalSupply()

# coins(i) — two known signatures; we try uint256 first, fall back to int128.
SEL_COINS_UINT256     = "0xc66106f8"
SEL_COINS_INT128      = "0xc6610657"

# balances(i) — same pattern but reversed (uint256 most common in Vyper pools).
SEL_BALANCES_UINT256  = "0x4903b0d1"
SEL_BALANCES_INT128   = "0x065c4a23"


from ._abi import decode_address as _decode_address, pad_uint as _pad_uint  # noqa: E402


@cached(source_id="curve.virtual_price")
def get_virtual_price(chain: Chain, pool: Address, block: int) -> int:
    return int(eth_call(chain, pool, SEL_GET_VIRTUAL_PRICE, block), 16)


@cached(source_id="curve.total_supply")
def total_supply(chain: Chain, pool: Address, block: int) -> int:
    return int(eth_call(chain, pool, SEL_TOTAL_SUPPLY, block), 16)


@cached(source_id="curve.coin")
def coin_at(chain: Chain, pool: Address, idx: int, block: int) -> Address:
    """Returns the address of the i-th coin in the pool. Tries uint256 then int128."""
    arg = _pad_uint(idx)
    try:
        return _decode_address(eth_call(chain, pool, SEL_COINS_UINT256 + arg, block))
    except RPCError:
        return _decode_address(eth_call(chain, pool, SEL_COINS_INT128 + arg, block))


@cached(source_id="curve.balance")
def balance_at(chain: Chain, pool: Address, idx: int, block: int) -> int:
    """Returns the i-th coin's reserve in the pool, in raw units."""
    arg = _pad_uint(idx)
    try:
        return int(eth_call(chain, pool, SEL_BALANCES_UINT256 + arg, block), 16)
    except RPCError:
        return int(eth_call(chain, pool, SEL_BALANCES_INT128 + arg, block), 16)


def n_coins(chain: Chain, pool: Address, block: int, *, max_probe: int = 4) -> int:
    """Probe how many coins the pool holds. Walks `coin_at(i)` until reverting.

    Phase 2.A only sees 2-coin stableswap pools; bumped `max_probe` to 4 for
    future plain/3pool/4pool variants. If a future pool has *more* coins than
    ``max_probe``, this would silently truncate; instead we raise so the caller
    can bump the probe limit explicitly.
    """
    for i in range(max_probe):
        try:
            coin_at(chain, pool, i, block)
        except (RPCError, ValueError):
            return i
    raise ValueError(
        f"n_coins({pool.hex}, block={block}): pool has at least {max_probe} "
        f"coins; bump max_probe to count further. Pricing math currently "
        "assumes the registered Curve templates."
    )


# ----------------------------------------------------------------------------
# Liquidity events — AddLiquidity / RemoveLiquidity[Imbalance|One]
# ----------------------------------------------------------------------------

# Topic hashes for Vyper Curve "Plain Pool" 2-coin stableswap (matches the
# Grove AUSD/USDC pool at 0xe79c1c...). Hashes computed from the canonical
# event signatures and verified against well-known curve.fi pools. Different
# Curve template generations use different signatures — Phase 2.A covers
# only the 2-pool variant; multi-coin templates are Phase 2.B+.
TOPIC_ADD_LIQUIDITY                = "0x423f6495a08fc652425cf4ed0d1f9e37e571d9b9529b1c1c23cce780b2e7df0d"
TOPIC_REMOVE_LIQUIDITY             = "0x7c363854ccf79623411f8995b362bce5eddff18c927edc6f5dbbb5e05819a82c"
TOPIC_REMOVE_LIQUIDITY_IMBALANCE   = "0xb964b72f73f5ef5bf0fcc559239fc0ccf550fa6f4456cabd0aaaf7f02ae4f7e0"
TOPIC_REMOVE_LIQUIDITY_ONE         = "0x9e96dd3b997a2a257eec4df9bb6eaf626e206df5f543bd963682d143300be310"

_REMOVE_TOPICS = (
    TOPIC_REMOVE_LIQUIDITY,
    TOPIC_REMOVE_LIQUIDITY_IMBALANCE,
    TOPIC_REMOVE_LIQUIDITY_ONE,
)


@dataclass(frozen=True, slots=True)
class CurveLiquidityEvent:
    """One liquidity-add or liquidity-remove event for a 2-coin Curve pool.

    Amounts are signed: ``+`` on add, ``-`` on remove (across all remove
    variants). For ``RemoveLiquidityOne`` only one amount is non-zero;
    ``coin_index`` records which coin was withdrawn (``-1`` if not applicable).
    """
    block_number: int
    tx_hash: str
    log_index: int
    amount0: int
    amount1: int
    is_increase: bool
    coin_index: int = -1   # only meaningful for RemoveLiquidityOne


def _decode_curve_event(log: dict, n_coins_in_pool: int = 2) -> CurveLiquidityEvent:
    """Decode an AddLiquidity / RemoveLiquidity[*] log.

    All variants encode token_amounts as the first ``N`` data words.
    ``RemoveLiquidityOne`` is special — token_amount (LP shares burned) and
    coin_amount (single-coin payout); we read coin_amount and infer the
    coin index from a separate ``index`` data word when present (Vyper
    ``RemoveLiquidityOne`` has 4 data words: token_amount, coin_amount,
    token_supply, …; older variants emit just (token_amount, coin_amount,
    token_supply)). For Phase 2.A (par-stable 2-pool), assigning the
    withdrawal to either coin yields the same USD outflow, so we degrade
    to coin_index=-1 and fold the amount into amount0 (negative) — both
    legs are par-stable and the math nets to the same total.
    """
    topic0 = log["topics"][0].lower()
    is_increase = topic0 == TOPIC_ADD_LIQUIDITY
    sign = 1 if is_increase else -1
    data = log["data"].removeprefix("0x")

    block_number = int(log["blockNumber"], 16)
    log_index = int(log["logIndex"], 16)
    tx_hash = log["transactionHash"]

    if topic0 == TOPIC_REMOVE_LIQUIDITY_ONE:
        # data: [token_amount (LP burned), coin_amount, …optional]. coin_amount
        # is the second word.
        coin_amount = int(data[64:128], 16)
        return CurveLiquidityEvent(
            block_number=block_number, tx_hash=tx_hash, log_index=log_index,
            amount0=sign * coin_amount, amount1=0,
            is_increase=False, coin_index=0,   # see docstring caveat
        )
    # AddLiquidity / RemoveLiquidity / RemoveLiquidityImbalance — first N
    # data words are token_amounts[N].
    amounts = [
        int(data[i * 64 : (i + 1) * 64], 16)
        for i in range(n_coins_in_pool)
    ]
    return CurveLiquidityEvent(
        block_number=block_number, tx_hash=tx_hash, log_index=log_index,
        amount0=sign * (amounts[0] if len(amounts) > 0 else 0),
        amount1=sign * (amounts[1] if len(amounts) > 1 else 0),
        is_increase=is_increase,
    )


def read_liquidity_events(
    chain: Chain,
    pool: Address,
    provider: Address,
    from_block: int,
    to_block: int,
    *,
    n_coins_in_pool: int = 2,
) -> list[CurveLiquidityEvent]:
    """All Add/Remove liquidity events emitted by the pool with provider=indexed.

    Filters by ``topics[1] = padded(provider.address)`` to scope to one ALM.
    Subject to the same ``eth_getLogs`` rate-limits as the V3 inflow path
    (Alchemy free tier caps at 10 blocks/request); production runs need a
    Dune-backed implementation for full-month ranges.
    """
    if from_block > to_block:
        return []
    provider_topic = "0x" + provider.value.hex().rjust(64, "0")
    topics_to_walk = (
        TOPIC_ADD_LIQUIDITY,
        TOPIC_REMOVE_LIQUIDITY,
        TOPIC_REMOVE_LIQUIDITY_IMBALANCE,
        TOPIC_REMOVE_LIQUIDITY_ONE,
    )
    out: list[CurveLiquidityEvent] = []
    for topic0 in topics_to_walk:
        logs = eth_get_logs(
            chain, pool,
            topics=[topic0, provider_topic],
            from_block=from_block, to_block=to_block,
        )
        out.extend(_decode_curve_event(log, n_coins_in_pool) for log in logs)
    out.sort(key=lambda e: (e.block_number, e.log_index))
    return out
