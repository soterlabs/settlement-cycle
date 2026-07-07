"""Uniswap V4 position-pricer primitives.

V4 is a singleton architecture — unlike V3, there are no per-pool contracts.
All pools live inside one ``PoolManager`` keyed by ``poolId = keccak256(abi
.encode(PoolKey))``; LP positions are ERC-721 NFTs minted by a separate
``PositionManager``. Three structural differences from V3 drive this module
(see also ``extract/uniswap_v3.py``):

1. **poolId + storage reads.** Pool price (``slot0``) is read from the
   ``PoolManager`` via ``extsload`` against the pool's state slot
   ``keccak256(poolId ++ POOLS_SLOT)``. Needs keccak — see ``_keccak``.
2. **No ERC-721 enumeration.** ``PositionManager`` does not implement
   ``tokenOfOwnerByIndex``, so token ids cannot be discovered on-chain; the
   caller supplies them (from config). We still verify ownership + pool
   membership per token id at the snapshot block.
3. **Packed position info.** ``getPoolAndPositionInfo(tokenId)`` returns the
   full ``PoolKey`` plus a bit-packed ``info`` word (tickLower / tickUpper /
   truncated poolId); liquidity is a separate ``getPositionLiquidity`` call.

The **tick math** (``liquidity + sqrtPrice → amount0/amount1``) is identical
to V3, so we reuse ``uniswap_v3.get_sqrt_ratio_at_tick`` /
``get_amounts_for_liquidity`` rather than duplicating it.

Canonical Ethereum addresses (v4 launch deployment):
  PoolManager      0x000000000004444c5dc75cB358380D2e3dE08A90
  PositionManager  0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9E
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.primes import Address, Chain
from ._abi import pad_address as _pad_address, pad_uint as _pad_uint
from ._keccak import keccak256
from .cache import cached
from .rpc import eth_call, eth_get_logs

# Canonical v4 singletons (Ethereum mainnet launch deployment).
POOL_MANAGER_CANONICAL = Address.from_str("0x000000000004444c5dc75cB358380D2e3dE08A90")
POSITION_MANAGER_CANONICAL = Address.from_str("0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9E")

# Storage slot of the ``_pools`` mapping inside PoolManager (v4-core
# StateLibrary.POOLS_SLOT). slot0 is the first word of each pool's state.
POOLS_SLOT = 6


def _selector(signature: str) -> str:
    """First 4 bytes of keccak256(signature), as a ``0x``-prefixed hex string."""
    return "0x" + keccak256(signature.encode()).hex()[:8]


SEL_EXTSLOAD = "0x1e2eaeaf"                                    # extsload(bytes32)
SEL_OWNER_OF = "0x6352211e"                                    # ownerOf(uint256)
SEL_GET_POOL_AND_POSITION_INFO = _selector("getPoolAndPositionInfo(uint256)")
SEL_GET_POSITION_LIQUIDITY = _selector("getPositionLiquidity(uint256)")

# ModifyLiquidity(PoolId indexed id, address indexed sender, int24 tickLower,
#                 int24 tickUpper, int256 liquidityDelta, bytes32 salt)
TOPIC_MODIFY_LIQUIDITY = "0x" + keccak256(
    b"ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)"
).hex()


def _decode_int24(word_hex: str) -> int:
    raw = int(word_hex, 16) & ((1 << 24) - 1)
    return raw - (1 << 24) if raw >= (1 << 23) else raw


def _decode_int256(word_hex: str) -> int:
    raw = int(word_hex, 16)
    return raw - (1 << 256) if raw >= (1 << 255) else raw


@dataclass(frozen=True, slots=True)
class V4PoolKey:
    """Identifies a v4 pool. ``poolId`` is ``keccak256(abi.encode(self))``."""

    currency0: Address
    currency1: Address
    fee: int
    tick_spacing: int
    hooks: Address

    def pool_id(self) -> bytes:
        encoded = (
            bytes(12) + self.currency0.value
            + bytes(12) + self.currency1.value
            + self.fee.to_bytes(32, "big")
            + (self.tick_spacing & ((1 << 256) - 1)).to_bytes(32, "big")
            + bytes(12) + self.hooks.value
        )
        return keccak256(encoded)


@dataclass(frozen=True, slots=True)
class V4Slot0:
    sqrt_price_x96: int
    tick: int


@dataclass(frozen=True, slots=True)
class V4Position:
    """A v4 LP position at a snapshot block. ``None`` fields when the token id
    is not owned / not minted / belongs to a different pool."""

    token_id: int
    pool_key: V4PoolKey
    tick_lower: int
    tick_upper: int
    liquidity: int


@dataclass(frozen=True, slots=True)
class V4LiquidityEvent:
    """One ``ModifyLiquidity`` event. ``liquidity_delta`` is signed (+ add /
    − remove). ``salt`` is the position's token id (v4 PositionManager mints
    with ``salt = bytes32(tokenId)``)."""

    block_number: int
    tx_hash: str
    log_index: int
    token_id: int
    tick_lower: int
    tick_upper: int
    liquidity_delta: int


def _is_empty(raw: str | None) -> bool:
    return raw is None or raw in ("0x", "0x0", "")


@cached(source_id="uniswap_v4.slot0")
def read_slot0(chain: Chain, pool_manager: Address, pool_id: bytes, block: int) -> V4Slot0 | None:
    """Read ``(sqrtPriceX96, tick)`` for ``pool_id`` from the PoolManager via
    ``extsload``. Returns ``None`` if the pool is uninitialized at ``block``."""
    state_slot = keccak256(pool_id + POOLS_SLOT.to_bytes(32, "big"))
    data = SEL_EXTSLOAD + state_slot.hex()
    raw = eth_call(chain, pool_manager, data, block)
    if _is_empty(raw):
        return None
    word = int(raw, 16)
    sqrt_price_x96 = word & ((1 << 160) - 1)
    if sqrt_price_x96 == 0:
        return None
    tick_raw = (word >> 160) & ((1 << 24) - 1)
    tick = tick_raw - (1 << 24) if tick_raw >= (1 << 23) else tick_raw
    return V4Slot0(sqrt_price_x96=sqrt_price_x96, tick=tick)


@cached(source_id="uniswap_v4.owner_of")
def owner_of(chain: Chain, position_manager: Address, token_id: int, block: int) -> Address | None:
    """``ownerOf(tokenId)``. Returns ``None`` if the token is not minted /
    burned at ``block`` (call reverts → empty result)."""
    data = SEL_OWNER_OF + _pad_uint(token_id)
    raw = eth_call(chain, position_manager, data, block)
    if _is_empty(raw):
        return None
    return Address(bytes.fromhex(raw.removeprefix("0x")[-40:]))


@cached(source_id="uniswap_v4.pool_and_position_info")
def read_pool_and_position_info(
    chain: Chain, position_manager: Address, token_id: int, block: int,
) -> tuple[V4PoolKey, int, int] | None:
    """``getPoolAndPositionInfo(tokenId)`` → ``(PoolKey, tickLower, tickUpper)``.

    Returns ``None`` when the token id is not minted at ``block``. The packed
    ``info`` word layout (v4-periphery PositionInfoLibrary) is::

        bits  0..7   hasSubscriber
        bits  8..31  tickLower  (int24)
        bits 32..55  tickUpper  (int24)
        bits 56..255 poolId     (truncated)
    """
    data = SEL_GET_POOL_AND_POSITION_INFO + _pad_uint(token_id)
    raw = eth_call(chain, position_manager, data, block)
    if _is_empty(raw):
        return None
    h = raw.removeprefix("0x")

    def word(i: int) -> str:
        return h[i * 64:(i + 1) * 64]

    pool_key = V4PoolKey(
        currency0=Address(bytes.fromhex(word(0)[-40:])),
        currency1=Address(bytes.fromhex(word(1)[-40:])),
        fee=int(word(2), 16),
        tick_spacing=_decode_int24(word(3)),
        hooks=Address(bytes.fromhex(word(4)[-40:])),
    )
    info = int(word(5), 16)
    tick_lower = info >> 8 & 0xFFFFFF
    tick_upper = info >> 32 & 0xFFFFFF
    tick_lower = tick_lower - (1 << 24) if tick_lower >= (1 << 23) else tick_lower
    tick_upper = tick_upper - (1 << 24) if tick_upper >= (1 << 23) else tick_upper
    return pool_key, tick_lower, tick_upper


@cached(source_id="uniswap_v4.position_liquidity")
def read_position_liquidity(
    chain: Chain, position_manager: Address, token_id: int, block: int,
) -> int:
    """``getPositionLiquidity(tokenId)`` → uint128. 0 when not minted."""
    data = SEL_GET_POSITION_LIQUIDITY + _pad_uint(token_id)
    raw = eth_call(chain, position_manager, data, block)
    if _is_empty(raw):
        return 0
    return int(raw, 16)


def read_position(
    chain: Chain,
    position_manager: Address,
    token_id: int,
    block: int,
    *,
    holder: Address,
    expected_pool_id: bytes,
) -> V4Position | None:
    """Full position read for one token id, with ownership + pool filtering.

    Returns ``None`` (skip) when, at ``block``, the token id is not minted, is
    not owned by ``holder``, or belongs to a pool other than ``expected_pool_id``.
    """
    info = read_pool_and_position_info(chain, position_manager, token_id, block)
    if info is None:
        return None
    pool_key, tick_lower, tick_upper = info
    if pool_key.pool_id() != expected_pool_id:
        return None
    owner = owner_of(chain, position_manager, token_id, block)
    if owner is None or owner.value != holder.value:
        return None
    liquidity = read_position_liquidity(chain, position_manager, token_id, block)
    return V4Position(
        token_id=token_id,
        pool_key=pool_key,
        tick_lower=tick_lower,
        tick_upper=tick_upper,
        liquidity=liquidity,
    )


def read_modify_liquidity_events(
    chain: Chain,
    pool_manager: Address,
    pool_id: bytes,
    from_block: int,
    to_block: int,
    *,
    sender: Address | None = None,
) -> list[V4LiquidityEvent]:
    """All ``ModifyLiquidity`` events for ``pool_id`` in ``(from_block, to_block]``.

    Filtered on-chain by ``topics=[ModifyLiquidity, poolId]``; the per-position
    ``salt`` (= token id) is decoded from the data so callers can match against
    a known token-id set. Pagination handled by ``eth_get_logs``.

    ``sender`` (indexed topic 2) is the ``msg.sender`` to
    ``PoolManager.modifyLiquidity`` — for NFT positions this is the
    PositionManager, which is also the position ``owner`` in the pool's state
    key ``keccak(owner, tickLower, tickUpper, salt)``. Passing it scopes the
    query to positions owned by that manager and prevents mis-attribution:
    ``salt`` is only unique *per owner*, so a different LP in the same pool
    using a colliding salt (e.g. the common ``salt=0``, or another
    PositionManager's sequential token ids) would otherwise be matched by the
    caller's token-id filter. ``None`` leaves the query unscoped (all owners).
    """
    if from_block > to_block:
        return []
    topics: list[str] = [TOPIC_MODIFY_LIQUIDITY, "0x" + pool_id.hex()]
    if sender is not None:
        topics.append("0x" + _pad_address(sender))
    logs = eth_get_logs(
        chain, pool_manager,
        topics=topics,
        from_block=from_block, to_block=to_block,
    )
    out: list[V4LiquidityEvent] = []
    for log in logs:
        ev = decode_modify_liquidity_log(log)
        if ev is not None:
            out.append(ev)
    out.sort(key=lambda e: (e.block_number, e.log_index))
    return out


def decode_modify_liquidity_log(log: dict) -> V4LiquidityEvent | None:
    """Decode one raw ``ModifyLiquidity`` log dict into a ``V4LiquidityEvent``.

    ``log`` uses the RPC shape (``blockNumber``/``logIndex`` hex or int,
    ``data`` 0x-hex). Returns ``None`` for zero-delta logs (fee-collect /
    no-op modifies). Shared by the RPC scan above and the Dune-backed
    ``DuneUniswapV4FlowsSource``.
    """
    h = log["data"].removeprefix("0x")
    tick_lower = _decode_int24(h[0:64])
    tick_upper = _decode_int24(h[64:128])
    liquidity_delta = _decode_int256(h[128:192])
    salt = int(h[192:256], 16)
    if liquidity_delta == 0:
        return None
    def _as_int(v):
        return int(v, 16) if isinstance(v, str) else int(v)
    return V4LiquidityEvent(
        block_number=_as_int(log["blockNumber"]),
        tx_hash=log["transactionHash"],
        log_index=_as_int(log["logIndex"]),
        token_id=salt,
        tick_lower=tick_lower,
        tick_upper=tick_upper,
        liquidity_delta=liquidity_delta,
    )
