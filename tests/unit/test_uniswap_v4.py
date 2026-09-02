"""Unit tests for the Uniswap V4 primitives: keccak, poolId, packed position
info decode, slot0 decode, value composition, and leg decomposition."""

from __future__ import annotations

from decimal import Decimal

import pytest

from settle.domain import Address, Chain, PricingCategory, Token, Venue
from settle.domain.period import Period
from settle.domain.primes import CurveIdleUsdsConfig, Prime, UniV4PoolKey
from settle.extract._keccak import keccak256
from settle.extract import uniswap_v4 as v4
from settle.normalize.positions import _uniswap_v4_value, _venue_v4_pool_key
from settle.normalize.prices import UnsupportedPricingError
from settle.normalize.sources.uniswap_v4 import (
    RPCUniswapV4PositionSource,
    V4PositionAmounts,
)

USDS = Address.from_str("0xdc035d45d973e3ec169d2276ddab16f1e407384f")
PYUSD = Address.from_str("0x6c3ea9036406852006290770bedfcaba0e23a0e8")
USDT = Address.from_str("0xdac17f958d2ee523a2206206994597c13d831ec7")
ZERO = Address.from_str("0x0000000000000000000000000000000000000000")
ALM = Address.from_str("0x1601843c5e9bc251a3272907010afa41fa18347e")


# ----------------------------------------------------------------------------
# keccak-256 known-answer tests
# ----------------------------------------------------------------------------

def test_keccak_empty():
    assert keccak256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )


def test_keccak_abc():
    assert keccak256(b"abc").hex() == (
        "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    )


def test_keccak_transfer_topic():
    """keccak256 of the canonical ERC-20 Transfer signature == the known topic0."""
    assert keccak256(b"Transfer(address,address,uint256)").hex() == (
        "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    )


# ----------------------------------------------------------------------------
# poolId = keccak256(abi.encode(PoolKey))
# ----------------------------------------------------------------------------

def test_pool_id_matches_onchain_pyusd_usds():
    """PoolId for the live Spark PYUSD/USDS v4 pool (verified on-chain)."""
    key = v4.V4PoolKey(currency0=PYUSD, currency1=USDS, fee=5, tick_spacing=1, hooks=ZERO)
    assert key.pool_id().hex() == (
        "e63e32b2ae40601662f760d6bf5d771057324fbd97784fe1d3717069f7b75d45"
    )


def test_pool_id_changes_with_fee():
    a = v4.V4PoolKey(PYUSD, USDS, 5, 1, ZERO).pool_id()
    b = v4.V4PoolKey(PYUSD, USDS, 100, 1, ZERO).pool_id()
    assert a != b


def test_selectors_derived_from_signatures():
    assert v4.SEL_GET_POOL_AND_POSITION_INFO == "0x7ba03aad"
    assert v4.SEL_GET_POSITION_LIQUIDITY.startswith("0x")


# ----------------------------------------------------------------------------
# slot0 decode (extsload word: sqrtPriceX96 | tick<<160 | ...)
# ----------------------------------------------------------------------------

def _slot0_word(sqrt_price: int, tick: int) -> str:
    tick_field = tick & ((1 << 24) - 1)
    word = sqrt_price | (tick_field << 160)
    return "0x" + format(word, "x").rjust(64, "0")


def test_read_slot0_decodes_price_and_positive_tick(monkeypatch):
    monkeypatch.setenv("SETTLE_NO_CACHE", "1")
    sqrt_price = 79237242843662870362935403847366830
    monkeypatch.setattr(v4, "eth_call", lambda *a, **k: _slot0_word(sqrt_price, 276326))
    s = v4.read_slot0(Chain.ETHEREUM, v4.POOL_MANAGER_CANONICAL, b"\x00" * 32, 1)
    assert s.sqrt_price_x96 == sqrt_price
    assert s.tick == 276326


def test_read_slot0_sign_extends_negative_tick(monkeypatch):
    monkeypatch.setenv("SETTLE_NO_CACHE", "1")
    monkeypatch.setattr(v4, "eth_call", lambda *a, **k: _slot0_word(1 << 96, -100))
    s = v4.read_slot0(Chain.ETHEREUM, v4.POOL_MANAGER_CANONICAL, b"\x00" * 32, 1)
    assert s.tick == -100


def test_read_slot0_none_when_uninitialized(monkeypatch):
    """sqrtPriceX96 == 0 (pre-deployment) → None, so callers degrade to $0."""
    monkeypatch.setenv("SETTLE_NO_CACHE", "1")
    monkeypatch.setattr(v4, "eth_call", lambda *a, **k: "0x" + "0" * 64)
    assert v4.read_slot0(Chain.ETHEREUM, v4.POOL_MANAGER_CANONICAL, b"\x00" * 32, 1) is None


def test_read_slot0_none_on_empty_result(monkeypatch):
    monkeypatch.setenv("SETTLE_NO_CACHE", "1")
    monkeypatch.setattr(v4, "eth_call", lambda *a, **k: "0x")
    assert v4.read_slot0(Chain.ETHEREUM, v4.POOL_MANAGER_CANONICAL, b"\x00" * 32, 1) is None


# ----------------------------------------------------------------------------
# packed PositionInfo decode (getPoolAndPositionInfo)
# ----------------------------------------------------------------------------

def _addr_word(a: Address) -> str:
    return a.value.hex().rjust(64, "0")


def _pool_and_info_return(key: v4.V4PoolKey, tick_lower: int, tick_upper: int) -> str:
    info = (
        ((tick_upper & ((1 << 24) - 1)) << 32)
        | ((tick_lower & ((1 << 24) - 1)) << 8)
    )
    words = [
        _addr_word(key.currency0),
        _addr_word(key.currency1),
        format(key.fee, "x").rjust(64, "0"),
        format(key.tick_spacing & ((1 << 256) - 1), "x").rjust(64, "0"),
        _addr_word(key.hooks),
        format(info, "x").rjust(64, "0"),
    ]
    return "0x" + "".join(words)


def test_read_pool_and_position_info_decodes_positive_ticks(monkeypatch):
    monkeypatch.setenv("SETTLE_NO_CACHE", "1")
    key = v4.V4PoolKey(PYUSD, USDS, 5, 1, ZERO)
    monkeypatch.setattr(
        v4, "eth_call",
        lambda *a, **k: _pool_and_info_return(key, 276322, 276326),
    )
    pk, tl, tu = v4.read_pool_and_position_info(Chain.ETHEREUM, v4.POSITION_MANAGER_CANONICAL, 161205, 1)
    assert pk == key
    assert (tl, tu) == (276322, 276326)


def test_read_pool_and_position_info_sign_extends_negative_ticks(monkeypatch):
    monkeypatch.setenv("SETTLE_NO_CACHE", "1")
    key = v4.V4PoolKey(PYUSD, USDS, 5, 1, ZERO)
    monkeypatch.setattr(
        v4, "eth_call",
        lambda *a, **k: _pool_and_info_return(key, -50, 50),
    )
    _pk, tl, tu = v4.read_pool_and_position_info(Chain.ETHEREUM, v4.POSITION_MANAGER_CANONICAL, 1, 1)
    assert (tl, tu) == (-50, 50)


def test_read_pool_and_position_info_none_when_not_minted(monkeypatch):
    monkeypatch.setenv("SETTLE_NO_CACHE", "1")
    monkeypatch.setattr(v4, "eth_call", lambda *a, **k: "0x")
    assert v4.read_pool_and_position_info(Chain.ETHEREUM, v4.POSITION_MANAGER_CANONICAL, 1, 1) is None


# ----------------------------------------------------------------------------
# read_position — ownership + pool filtering
# ----------------------------------------------------------------------------

def _patch_position(monkeypatch, *, key, tick_lower, tick_upper, owner, liquidity):
    monkeypatch.setattr(
        v4, "read_pool_and_position_info",
        lambda *a, **k: (key, tick_lower, tick_upper),
    )
    monkeypatch.setattr(v4, "owner_of", lambda *a, **k: owner)
    monkeypatch.setattr(v4, "read_position_liquidity", lambda *a, **k: liquidity)


def test_read_position_ok(monkeypatch):
    key = v4.V4PoolKey(PYUSD, USDS, 5, 1, ZERO)
    _patch_position(monkeypatch, key=key, tick_lower=1, tick_upper=2, owner=ALM, liquidity=999)
    pos = v4.read_position(
        Chain.ETHEREUM, v4.POSITION_MANAGER_CANONICAL, 161205, 1,
        holder=ALM, expected_pool_id=key.pool_id(),
    )
    assert pos is not None and pos.liquidity == 999


def test_read_position_skips_wrong_pool(monkeypatch):
    key = v4.V4PoolKey(PYUSD, USDS, 5, 1, ZERO)
    other = v4.V4PoolKey(USDT, USDS, 5, 1, ZERO)
    _patch_position(monkeypatch, key=other, tick_lower=1, tick_upper=2, owner=ALM, liquidity=999)
    pos = v4.read_position(
        Chain.ETHEREUM, v4.POSITION_MANAGER_CANONICAL, 1, 1,
        holder=ALM, expected_pool_id=key.pool_id(),
    )
    assert pos is None


def test_read_position_skips_wrong_owner(monkeypatch):
    key = v4.V4PoolKey(PYUSD, USDS, 5, 1, ZERO)
    _patch_position(monkeypatch, key=key, tick_lower=1, tick_upper=2, owner=PYUSD, liquidity=999)
    pos = v4.read_position(
        Chain.ETHEREUM, v4.POSITION_MANAGER_CANONICAL, 1, 1,
        holder=ALM, expected_pool_id=key.pool_id(),
    )
    assert pos is None


def test_read_position_none_when_not_minted(monkeypatch):
    key = v4.V4PoolKey(PYUSD, USDS, 5, 1, ZERO)
    monkeypatch.setattr(v4, "read_pool_and_position_info", lambda *a, **k: None)
    pos = v4.read_position(
        Chain.ETHEREUM, v4.POSITION_MANAGER_CANONICAL, 1, 1,
        holder=ALM, expected_pool_id=key.pool_id(),
    )
    assert pos is None


# ----------------------------------------------------------------------------
# ModifyLiquidity event decode + signed flows
# ----------------------------------------------------------------------------

def _hex_word(n: int) -> str:
    return format(n & ((1 << 256) - 1), "x").rjust(64, "0")


def _modify_log(pool_id: bytes, tick_lower, tick_upper, liquidity_delta, salt,
                *, block=1000, log_index=0):
    return {
        "blockNumber": hex(block),
        "transactionHash": "0xabcd",
        "logIndex": hex(log_index),
        "topics": [v4.TOPIC_MODIFY_LIQUIDITY, "0x" + pool_id.hex()],
        "data": "0x" + _hex_word(tick_lower) + _hex_word(tick_upper)
                + _hex_word(liquidity_delta) + _hex_word(salt),
    }


def test_read_modify_liquidity_events_decodes_and_skips_noop(monkeypatch):
    key = v4.V4PoolKey(PYUSD, USDS, 5, 1, ZERO)
    pid = key.pool_id()
    logs = [
        _modify_log(pid, 100, 200, 5000, 161205, log_index=0),
        _modify_log(pid, 100, 200, -3000, 161205, log_index=1),
        _modify_log(pid, 100, 200, 0, 161205, log_index=2),   # fee collect — skipped
    ]
    monkeypatch.setattr(v4, "eth_get_logs", lambda *a, **k: logs)
    evs = v4.read_modify_liquidity_events(Chain.ETHEREUM, v4.POOL_MANAGER_CANONICAL, pid, 1, 2000)
    assert [e.liquidity_delta for e in evs] == [5000, -3000]
    assert all(e.token_id == 161205 for e in evs)


def test_read_modify_liquidity_events_scopes_by_sender(monkeypatch):
    """When ``sender`` is passed it becomes indexed topic 2 (left-padded);
    omitting it leaves the query unscoped to two topics."""
    key = v4.V4PoolKey(PYUSD, USDS, 5, 1, ZERO)
    pid = key.pool_id()
    captured: dict = {}
    monkeypatch.setattr(
        v4, "eth_get_logs",
        lambda *a, **k: (captured.update(k), [])[1],
    )

    v4.read_modify_liquidity_events(
        Chain.ETHEREUM, v4.POOL_MANAGER_CANONICAL, pid, 1, 2000,
        sender=v4.POSITION_MANAGER_CANONICAL,
    )
    topics = captured["topics"]
    assert topics[0] == v4.TOPIC_MODIFY_LIQUIDITY
    assert topics[1] == "0x" + pid.hex()
    assert topics[2] == "0x" + "0" * 24 + v4.POSITION_MANAGER_CANONICAL.value.hex()

    v4.read_modify_liquidity_events(
        Chain.ETHEREUM, v4.POOL_MANAGER_CANONICAL, pid, 1, 2000,
    )
    assert len(captured["topics"]) == 2


def test_liquidity_flows_scopes_events_to_position_manager(monkeypatch):
    """The inflow path must pass its PositionManager as the event ``sender``
    (owner), honoring a per-chain override so it matches the value path."""
    key = v4.V4PoolKey(PYUSD, USDS, 5, 1, ZERO)
    captured: dict = {}
    monkeypatch.setattr(
        v4, "read_modify_liquidity_events",
        lambda *a, **k: (captured.update(k), [])[1],
    )

    RPCUniswapV4PositionSource().liquidity_flows_in_pool(
        chain="ethereum", token_ids=[161205], pool_key=key, from_block=1, to_block=2000,
    )
    assert captured["sender"] == v4.POSITION_MANAGER_CANONICAL

    custom = Address.from_str("0x" + "ab" * 20)
    RPCUniswapV4PositionSource(
        position_manager_per_chain={Chain.ETHEREUM: custom},
    ).liquidity_flows_in_pool(
        chain="ethereum", token_ids=[161205], pool_key=key, from_block=1, to_block=2000,
    )
    assert captured["sender"] == custom


def test_liquidity_flows_sign_and_filter(monkeypatch):
    key = v4.V4PoolKey(PYUSD, USDS, 5, 1, ZERO)
    pid = key.pool_id()
    events = [
        v4.V4LiquidityEvent(1000, "0x", 0, 161205, 100, 200, 5000),
        v4.V4LiquidityEvent(1001, "0x", 0, 999999, 100, 200, 5000),   # not our token id
    ]
    monkeypatch.setattr(v4, "read_modify_liquidity_events", lambda *a, **k: events)
    from settle.extract.uniswap_v3 import get_sqrt_ratio_at_tick
    monkeypatch.setattr(
        v4, "read_slot0",
        lambda *a, **k: v4.V4Slot0(sqrt_price_x96=get_sqrt_ratio_at_tick(150), tick=150),
    )
    src = RPCUniswapV4PositionSource()
    flows = src.liquidity_flows_in_pool(
        chain="ethereum", token_ids=[161205], pool_key=key, from_block=1, to_block=2000,
    )
    assert len(flows) == 1
    assert flows[0].token_id == 161205
    # in-range add → both legs positive
    assert flows[0].amount0 > 0 and flows[0].amount1 > 0


# ----------------------------------------------------------------------------
# _uniswap_v4_value — composition with a mock source
# ----------------------------------------------------------------------------

def _v4_venue(currency0=PYUSD, currency1=USDS, token_ids=(161205,)) -> Venue:
    return Venue(
        id="S61", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, v4.POSITION_MANAGER_CANONICAL, "UNIV4", 18),
        pricing_category=PricingCategory.LP_POOL,
        lp_kind="uniswap_v4",
        nft_position_manager=v4.POSITION_MANAGER_CANONICAL,
        univ4_pool_key=UniV4PoolKey(currency0, currency1, 5, 1, ZERO),
        univ4_token_ids=tuple(token_ids),
        curve_idle_usds=CurveIdleUsdsConfig(coin=USDS, sde_coin=currency0),
    )


def _prime() -> Prime:
    from datetime import date
    return Prime(id="spark", ilk_bytes32=b"\x00" * 32, start_date=date(2024, 11, 18),
                 alm={Chain.ETHEREUM: ALM})


class _MockV4Source:
    def __init__(self, positions): self.positions = positions
    def positions_in_pool(self, chain, owner, token_ids, pool_key, block):
        return self.positions


def test_v4_value_zero_when_no_positions():
    src = _MockV4Source([])
    assert _uniswap_v4_value(_prime(), _v4_venue(), block=1, source=src) == Decimal("0")


def test_v4_value_sums_par_stables():
    """PYUSD (6dp) + USDS (18dp) summed at par."""
    src = _MockV4Source([
        V4PositionAmounts(161205, PYUSD, USDS,
                          amount0=400_000 * 10**6, amount1=600_000 * 10**18),
    ])
    assert _uniswap_v4_value(_prime(), _v4_venue(), block=1, source=src) == Decimal("1000000")


def test_v4_value_skips_zero_legs():
    src = _MockV4Source([
        V4PositionAmounts(161205, PYUSD, USDS, amount0=0, amount1=750_000 * 10**18),
    ])
    assert _uniswap_v4_value(_prime(), _v4_venue(), block=1, source=src) == Decimal("750000")


def test_v4_value_aggregates_positions():
    src = _MockV4Source([
        V4PositionAmounts(1, PYUSD, USDS, amount0=1_000_000 * 10**6, amount1=0),
        V4PositionAmounts(2, PYUSD, USDS, amount0=0, amount1=2_000_000 * 10**18),
    ])
    assert _uniswap_v4_value(_prime(), _v4_venue(), block=1, source=src) == Decimal("3000000")


def test_v4_value_raises_on_rpc_failure():
    """A pool read that FAILS (after retries) must block the run, not silently
    book $0 — the latter would corrupt the SoM/EoM MtM for a funded position.
    (Not-deployed venues return an empty list → $0 and never reach here.)"""
    from settle.extract.rpc import RPCError

    class _FailingSource:
        def positions_in_pool(self, chain, owner, token_ids, pool_key, block):
            raise RPCError("simulated sustained RPC failure")

    with pytest.raises(RPCError):
        _uniswap_v4_value(_prime(), _v4_venue(), block=1, source=_FailingSource())


def test_v4_inflow_timeseries_raises_on_rpc_failure():
    """A failed flows read must block the run, not degrade to an empty inflow
    frame — a dropped mid-period mint would be booked as revenue in
    ``revenue = Δvalue − Σ inflow``. (A range with genuinely no events still
    returns the empty frame.)"""
    from settle.extract.rpc import RPCError
    from settle.normalize.positions import _uniswap_v4_inflow_timeseries

    class _FailingFlowsSource:
        def liquidity_flows_in_pool(self, chain, token_ids, pool_key,
                                    from_block, to_block):
            raise RPCError("simulated sustained RPC failure")

    with pytest.raises(RPCError):
        _uniswap_v4_inflow_timeseries(
            _prime(), _v4_venue(), from_block=1, to_block=2000,
            source=_FailingFlowsSource(), block_to_date=lambda b: None,
        )


def test_v4_inflow_timeseries_empty_on_no_events():
    """No events in range is NOT a failure — it's a legitimate $0 inflow."""
    from settle.normalize.positions import _uniswap_v4_inflow_timeseries

    class _NoFlowsSource:
        def liquidity_flows_in_pool(self, chain, token_ids, pool_key,
                                    from_block, to_block):
            return []

    df = _uniswap_v4_inflow_timeseries(
        _prime(), _v4_venue(), from_block=1, to_block=2000,
        source=_NoFlowsSource(), block_to_date=lambda b: None,
    )
    assert len(df) == 0
    assert list(df.columns) == ["block_date", "daily_inflow", "cum_inflow"]


def test_v4_value_raises_on_non_par_stable():
    unknown = Address.from_str("0x" + "11" * 20)
    src = _MockV4Source([
        V4PositionAmounts(1, unknown, USDS, amount0=10**18, amount1=0),
    ])
    with pytest.raises(UnsupportedPricingError, match="par-stable registry"):
        _uniswap_v4_value(_prime(), _v4_venue(currency0=unknown), block=1, source=src)


# ----------------------------------------------------------------------------
# _univ4_position_legs — idle (USDS) vs SDE (PYUSD/USDT) split
# ----------------------------------------------------------------------------

def test_position_legs_split_idle_and_sde():
    from settle.compute.monthly_pnl import _univ4_position_legs
    venue = _v4_venue(currency0=PYUSD, currency1=USDS)  # idle=USDS, sde=PYUSD
    positions = [
        V4PositionAmounts(161205, PYUSD, USDS,
                          amount0=300_000 * 10**6, amount1=700_000 * 10**18),
    ]
    idle, sde = _univ4_position_legs(venue, positions)
    assert idle == Decimal("700000")   # USDS leg
    assert sde == Decimal("300000")    # PYUSD leg


def test_position_legs_usdt_pool():
    from settle.compute.monthly_pnl import _univ4_position_legs
    venue = _v4_venue(currency0=USDT, currency1=USDS)  # idle=USDS, sde=USDT
    positions = [
        V4PositionAmounts(1, USDT, USDS, amount0=20_000 * 10**6, amount1=0),
        V4PositionAmounts(2, USDT, USDS, amount0=0, amount1=80_000 * 10**18),
    ]
    idle, sde = _univ4_position_legs(venue, positions)
    assert idle == Decimal("80000")
    assert sde == Decimal("20000")


def test_venue_v4_pool_key_builder_roundtrip():
    venue = _v4_venue()
    k = _venue_v4_pool_key(venue)
    assert isinstance(k, v4.V4PoolKey)
    assert k.currency0 == PYUSD and k.currency1 == USDS and k.fee == 5


def test_position_legs_raises_on_unsupported_token():
    """A non-zero leg in a coin absent from KNOWN_PAR_STABLES_ETHEREUM is a
    scope/config error — fail loud like the Curve pool, don't silently skip."""
    from settle.compute.monthly_pnl import _univ4_position_legs

    unknown = Address.from_str("0x" + "11" * 20)
    venue = _v4_venue(currency0=unknown, currency1=USDS)
    positions = [
        V4PositionAmounts(1, unknown, USDS, amount0=10**18, amount1=700_000 * 10**18),
    ]
    with pytest.raises(UnsupportedPricingError, match="KNOWN_PAR_STABLES_ETHEREUM"):
        _univ4_position_legs(venue, positions)


# ----------------------------------------------------------------------------
# carry-forward on transient RPC failure (idle + SDE daily aggregators)
# ----------------------------------------------------------------------------

class _FlakyV4Source:
    """Returns fixed positions for the first ``ok_calls`` reads, then raises."""

    def __init__(self, positions, ok_calls):
        self.positions = positions
        self.ok_calls = ok_calls
        self.calls = 0

    def positions_in_pool(self, chain, owner, token_ids, pool_key, block):
        from settle.extract.rpc import RPCError
        self.calls += 1
        if self.calls > self.ok_calls:
            raise RPCError("simulated transient RPC failure")
        return self.positions


class _StubResolver:
    def block_at_or_before(self, chain, eod):
        return 100


def _period_3day():
    from datetime import date
    return Period(date(2025, 1, 1), date(2025, 1, 3))


def _idle_prime():
    from datetime import date
    return Prime(
        id="spark", ilk_bytes32=b"\x00" * 32, start_date=date(2024, 11, 18),
        alm={Chain.ETHEREUM: ALM}, venues=[_v4_venue(currency0=USDT, currency1=USDS)],
    )


def test_univ4_idle_carries_forward_on_rpc_failure():
    """Day-1 succeeds ($700k USDS leg); days 2–3 fail transiently and must
    carry the last-known value rather than booking $0."""
    from settle.compute.monthly_pnl import _aggregate_univ4_idle_usds

    src = _FlakyV4Source(
        [V4PositionAmounts(1, USDT, USDS, amount0=0, amount1=700_000 * 10**18)],
        ok_calls=1,
    )
    df, tw_avg = _aggregate_univ4_idle_usds(
        _idle_prime(), _period_3day(), v4_source=src, block_resolver=_StubResolver(),
    )
    assert list(df["cum_balance"]) == [Decimal("700000")] * 3
    # The per-venue time-weighted average is surfaced so the CoF
    # re-attribution can exclude the idle USDS leg from the allocation base
    # (it is already deducted from utilized daily). Flat $700k over 3 days.
    assert tw_avg == {"S61": Decimal("700000")}


def test_univ4_idle_raises_when_first_day_fails():
    """No prior value to carry → propagate rather than seed a month of $0."""
    from settle.compute.monthly_pnl import _aggregate_univ4_idle_usds
    from settle.extract.rpc import RPCError

    src = _FlakyV4Source([], ok_calls=0)
    with pytest.raises(RPCError):
        _aggregate_univ4_idle_usds(
            _idle_prime(), _period_3day(),
            v4_source=src, block_resolver=_StubResolver(),
        )


def test_univ4_sde_carries_forward_on_rpc_failure():
    """SDE leg (USDT) carries forward on transient failure."""
    from settle.compute.monthly_pnl import _univ4_sde_asset_value_timeseries

    venue = _v4_venue(currency0=USDT, currency1=USDS)  # sde=USDT
    src = _FlakyV4Source(
        [V4PositionAmounts(1, USDT, USDS, amount0=250_000 * 10**6, amount1=0)],
        ok_calls=1,
    )
    df = _univ4_sde_asset_value_timeseries(
        _idle_prime(), venue, _period_3day(),
        v4_source=src, block_resolver=_StubResolver(),
    )
    assert list(df["uncapped_value"]) == [Decimal("250000")] * 3
