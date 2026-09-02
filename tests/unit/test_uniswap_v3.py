"""Unit tests for the Uniswap V3 tick math + position pricing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from settle.domain import Address, Chain, PricingCategory, Token, Venue
from settle.extract.uniswap_v3 import (
    MAX_TICK,
    MIN_TICK,
    TOPIC_DECREASE_LIQUIDITY,
    TOPIC_INCREASE_LIQUIDITY,
    _decode_liquidity_log,
    compute_pending_fees,
    get_amounts_for_liquidity,
    get_sqrt_ratio_at_tick,
)
from settle.normalize.positions import _uniswap_v3_value
from settle.normalize.sources.uniswap_v3 import V3PositionAmounts
from settle.normalize.prices import UnsupportedPricingError


def _addr(seed: str) -> Address:
    return Address.from_str("0x" + seed.ljust(40, "0"))


# ----------------------------------------------------------------------------
# Tick math — sqrt-ratio identities
# ----------------------------------------------------------------------------

# Q64.96 sentinel: tick 0 → sqrtPriceX96 = 1.0 × 2^96.
_TICK0_RATIO = 1 << 96


def test_get_sqrt_ratio_at_tick_zero():
    """Tick 0 corresponds to price 1, so sqrtPrice = 1 × 2^96 (exactly)."""
    assert get_sqrt_ratio_at_tick(0) == _TICK0_RATIO


def test_get_sqrt_ratio_at_tick_symmetric():
    """`getSqrtRatioAtTick(-tick)` is the multiplicative inverse of
    `getSqrtRatioAtTick(tick)` in Q96.96. Inversion truncates so a few wei of
    rounding accumulates per ratio — assert the *relative* error in
    ``pos × neg / 2^192`` is below 1e-25 (well above noise but still tight)."""
    for t in [1, 100, 1000, 10000, 100000]:
        pos = get_sqrt_ratio_at_tick(t)
        neg = get_sqrt_ratio_at_tick(-t)
        product = pos * neg
        target = 1 << 192
        rel_err = abs(product - target) / target
        assert rel_err < 1e-25, f"tick ±{t}: relative error {rel_err:.2e} too large"


def test_get_sqrt_ratio_at_tick_monotonic():
    """Ratios must be strictly increasing in tick."""
    prev = get_sqrt_ratio_at_tick(-100)
    for t in range(-99, 101):
        cur = get_sqrt_ratio_at_tick(t)
        assert cur > prev
        prev = cur


def test_get_sqrt_ratio_at_tick_rejects_out_of_bounds():
    with pytest.raises(ValueError, match="out of range"):
        get_sqrt_ratio_at_tick(MIN_TICK - 1)
    with pytest.raises(ValueError, match="out of range"):
        get_sqrt_ratio_at_tick(MAX_TICK + 1)


# ----------------------------------------------------------------------------
# get_amounts_for_liquidity — three-way price branch
# ----------------------------------------------------------------------------

def test_amounts_below_range_all_in_token0():
    """Current price below tickLower → liquidity expressed entirely as token0."""
    sqrt_a = get_sqrt_ratio_at_tick(-100)
    sqrt_b = get_sqrt_ratio_at_tick(100)
    sqrt_p = get_sqrt_ratio_at_tick(-200)        # below
    a0, a1 = get_amounts_for_liquidity(sqrt_p, sqrt_a, sqrt_b, liquidity=10**18)
    assert a0 > 0
    assert a1 == 0


def test_amounts_above_range_all_in_token1():
    """Current price above tickUpper → liquidity entirely as token1."""
    sqrt_a = get_sqrt_ratio_at_tick(-100)
    sqrt_b = get_sqrt_ratio_at_tick(100)
    sqrt_p = get_sqrt_ratio_at_tick(200)         # above
    a0, a1 = get_amounts_for_liquidity(sqrt_p, sqrt_a, sqrt_b, liquidity=10**18)
    assert a0 == 0
    assert a1 > 0


def test_amounts_in_range_split():
    """In-range position has non-zero amounts in both tokens."""
    sqrt_a = get_sqrt_ratio_at_tick(-100)
    sqrt_b = get_sqrt_ratio_at_tick(100)
    sqrt_p = get_sqrt_ratio_at_tick(0)           # center of range
    a0, a1 = get_amounts_for_liquidity(sqrt_p, sqrt_a, sqrt_b, liquidity=10**18)
    assert a0 > 0
    assert a1 > 0
    # symmetric range, centered price → roughly equal split
    ratio = a0 / a1 if a1 else 0
    assert 0.9 < ratio < 1.1


def test_amounts_at_lower_edge_all_token0():
    """current_tick == tickLower → all token0 (sqrtP == sqrt_a, branch hits ≤)."""
    sqrt_a = get_sqrt_ratio_at_tick(-1)
    sqrt_b = get_sqrt_ratio_at_tick(1)
    a0, a1 = get_amounts_for_liquidity(sqrt_a, sqrt_a, sqrt_b, liquidity=10**18)
    assert a0 > 0
    assert a1 == 0


# ----------------------------------------------------------------------------
# _uniswap_v3_value — composition with mock source
# ----------------------------------------------------------------------------

def _grove_v3_venue() -> Venue:
    pool_addr = Address.from_str("0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d")
    return Venue(
        id="E12", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, pool_addr, "AUSDUSDC-UNI3", 0),
        pricing_category=PricingCategory.LP_POOL,
        lp_kind="uniswap_v3",
    )


def _grove_prime():
    """Tiny Prime instance just for V3 value testing."""
    from datetime import date
    from settle.domain.primes import Prime
    return Prime(
        id="grove",
        ilk_bytes32=b"\x00" * 32,
        start_date=date(2025, 5, 14),
        alm={Chain.ETHEREUM: Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")},
    )


class _MockV3Source:
    def __init__(self, positions): self.positions = positions; self.calls = []
    def positions_in_pool(self, chain, owner, pool, block):
        self.calls.append((chain, owner, pool, block))
        return self.positions


def test_v3_value_zero_when_no_positions():
    src = _MockV3Source(positions=[])
    val = _uniswap_v3_value(_grove_prime(), _grove_v3_venue(), block=1, source=src)
    assert val == Decimal("0")


def test_v3_value_sums_par_stable_amounts():
    """One position with 12.4M AUSD + 12.6M USDC → $25M (both par stables, 6 dec)."""
    AUSD = Address.from_str("0x00000000efe302beaa2b3e6e1b18d08d69a9012a")
    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    src = _MockV3Source(positions=[
        V3PositionAmounts(
            token_id=1192575, token0=AUSD, token1=USDC,
            amount0=12_400_000 * 10**6,
            amount1=12_600_000 * 10**6,
        ),
    ])
    val = _uniswap_v3_value(_grove_prime(), _grove_v3_venue(), block=1, source=src)
    assert val == Decimal("25000000")


def test_v3_value_raises_when_token_not_par_stable():
    """Pool with a yield-bearing or unknown coin → raises clearly."""
    UNKNOWN = Address.from_str("0x" + "11" * 20)
    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    src = _MockV3Source(positions=[
        V3PositionAmounts(token_id=1, token0=UNKNOWN, token1=USDC,
                          amount0=10**18, amount1=0),
    ])
    with pytest.raises(UnsupportedPricingError, match="par-stable registry"):
        _uniswap_v3_value(_grove_prime(), _grove_v3_venue(), block=1, source=src)


def test_v3_value_skips_zero_amount_legs():
    """A position fully on one side of the range has amount1=0 — must not
    blow up looking up token1 in the registry."""
    AUSD = Address.from_str("0x00000000efe302beaa2b3e6e1b18d08d69a9012a")
    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    src = _MockV3Source(positions=[
        V3PositionAmounts(
            token_id=1, token0=AUSD, token1=USDC,
            amount0=5_000_000 * 10**6,
            amount1=0,
        ),
    ])
    val = _uniswap_v3_value(_grove_prime(), _grove_v3_venue(), block=1, source=src)
    assert val == Decimal("5000000")


def test_v3_value_aggregates_multiple_positions():
    AUSD = Address.from_str("0x00000000efe302beaa2b3e6e1b18d08d69a9012a")
    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    src = _MockV3Source(positions=[
        V3PositionAmounts(token_id=1, token0=AUSD, token1=USDC,
                          amount0=1_000_000 * 10**6, amount1=0),
        V3PositionAmounts(token_id=2, token0=AUSD, token1=USDC,
                          amount0=0, amount1=2_000_000 * 10**6),
        V3PositionAmounts(token_id=3, token0=AUSD, token1=USDC,
                          amount0=500_000 * 10**6, amount1=500_000 * 10**6),
    ])
    val = _uniswap_v3_value(_grove_prime(), _grove_v3_venue(), block=1, source=src)
    assert val == Decimal("4000000")


# ----------------------------------------------------------------------------
# compute_pending_fees — feeGrowthInside delta math
# ----------------------------------------------------------------------------

def _pending(*, current_tick, lower=-100, upper=100, fg0=0, fg1=0,
             lo_out_0=0, lo_out_1=0, up_out_0=0, up_out_1=0,
             last_inside_0=0, last_inside_1=0, liquidity=10**18):
    """Tiny helper for compute_pending_fees with sane defaults."""
    return compute_pending_fees(
        current_tick=current_tick,
        tick_lower=lower, tick_upper=upper,
        fee_growth_global_0_x128=fg0, fee_growth_global_1_x128=fg1,
        lower_outside_0_x128=lo_out_0, lower_outside_1_x128=lo_out_1,
        upper_outside_0_x128=up_out_0, upper_outside_1_x128=up_out_1,
        fee_growth_inside_0_last_x128=last_inside_0,
        fee_growth_inside_1_last_x128=last_inside_1,
        liquidity=liquidity,
    )


def test_pending_fees_zero_liquidity_returns_zero():
    """L=0 collapses the pending-fee formula to 0 regardless of accumulators."""
    a, b = _pending(current_tick=0, fg0=10**30, fg1=10**30, liquidity=0)
    assert (a, b) == (0, 0)


def test_pending_fees_no_growth_returns_zero():
    """All accumulators zero → no fees ever earned → 0 pending."""
    a, b = _pending(current_tick=0)
    assert (a, b) == (0, 0)


def test_pending_fees_in_range_simple():
    """In-range, both outside accumulators initialised to 0, last_inside=0
    → feeGrowthInside == feeGrowthGlobal → pending = global × L / 2^128.

    Sanity: fg0 = 1<<128 (i.e. 1.0 in Q128.128) and L=10^6 → pending=10^6.
    """
    a, b = _pending(
        current_tick=0,
        fg0=1 << 128,  # 1.0 in Q128.128
        fg1=2 << 128,  # 2.0
        liquidity=10**6,
    )
    assert a == 10**6
    assert b == 2 * 10**6


def test_pending_fees_below_range_subtracts_correctly():
    """current_tick < lower → "below" is global − lower_outside; "above" is
    upper_outside (current is below upper trivially). For freshly-initialized
    ticks (outside = 0) and current < lower, feeGrowthInside collapses to 0."""
    a, b = _pending(
        current_tick=-200, lower=-100, upper=100,
        fg0=5 << 128, fg1=5 << 128,
        lo_out_0=0, up_out_0=0,
    )
    # below = global - 0 = global; above = 0; inside = global - global - 0 = 0
    assert (a, b) == (0, 0)


def test_pending_fees_credits_only_delta_since_last_snapshot():
    """If last_inside_0 == feeGrowthInside_now, the delta is 0 → no pending."""
    a, b = _pending(
        current_tick=0, fg0=10 << 128, last_inside_0=10 << 128,
    )
    # token1 still grew from 0 (last) → 0 (global) = 0
    assert (a, b) == (0, 0)


def test_pending_fees_handles_uint256_wrap():
    """Solidity uses unchecked uint256 subtraction. If the running global has
    wrapped past 2^256 but the ``last`` snapshot hasn't, the delta still
    yields the correct pending amount via modular arithmetic."""
    UINT256 = 1 << 256
    # last snapshot was just before wrap; now feeGrowthInside is small → wrapped.
    last = UINT256 - 5
    fg = 5  # i.e. inside has grown by 10 modulo 2^256
    a, _ = _pending(
        current_tick=0, fg0=fg, last_inside_0=last, liquidity=1 << 128,
    )
    # delta = (5 - (UINT256-5)) mod 2^256 = 10 ; pending = (10 << 128) >> 128 = 10
    assert a == 10


# ----------------------------------------------------------------------------
# RPCUniswapV3PositionSource — fee-accrual integration with mocked extract layer
# ----------------------------------------------------------------------------

def test_position_source_adds_pending_fees_on_top_of_tokens_owed(monkeypatch):
    """A position with both materialized tokensOwed AND a non-zero
    feeGrowthInside delta should sum both into amount0/amount1."""
    from settle.extract import uniswap_v3 as v3
    from settle.normalize.sources.uniswap_v3 import RPCUniswapV3PositionSource

    AUSD = Address.from_str("0x00000000efe302beaa2b3e6e1b18d08d69a9012a")
    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    pool_addr = Address.from_str("0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d")
    owner = Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")

    pool_state = v3.V3PoolState(
        sqrt_price_x96=get_sqrt_ratio_at_tick(0),
        current_tick=0,
        token0=AUSD, token1=USDC, fee=100,
        fee_growth_global_0_x128=3 << 128,  # 3.0
        fee_growth_global_1_x128=0,
    )
    # L=0 makes liquidity-implied amounts both zero; we're testing fee-only path.
    # But the source skips read_tick when liquidity==0, so use small L.
    pos = v3.V3Position(
        token_id=42, token0=AUSD, token1=USDC, fee=100,
        tick_lower=-1, tick_upper=1, liquidity=1,
        fee_growth_inside_0_last_x128=1 << 128,  # snapshot was at 1.0
        fee_growth_inside_1_last_x128=0,
        tokens_owed_0=777_000_000,  # 777 USDC raw
        tokens_owed_1=0,
    )
    tick_zero = v3.V3TickInfo(fee_growth_outside_0_x128=0, fee_growth_outside_1_x128=0)

    monkeypatch.setattr(v3, "read_pool_state", lambda chain, pool, block: pool_state)
    monkeypatch.setattr(v3, "nfpm_balance_of", lambda chain, nfpm, owner, block: 1)
    monkeypatch.setattr(v3, "token_of_owner_by_index",
                        lambda chain, nfpm, owner, idx, block: 42)
    monkeypatch.setattr(v3, "read_position",
                        lambda chain, nfpm, token_id, block: pos)
    monkeypatch.setattr(v3, "read_tick",
                        lambda chain, pool, tick, block: tick_zero)

    src = RPCUniswapV3PositionSource()
    out = src.positions_in_pool(
        chain="ethereum", owner=owner.value, pool=pool_addr.value, block=1,
    )
    assert len(out) == 1
    # Pending: inside_now=3<<128, last=1<<128, delta=2<<128, L=1
    # pending_0 = (2<<128) * 1 >> 128 = 2
    # amount0 = liquidity-implied (≈0 with L=1) + tokensOwed (777e6) + pending (2)
    assert out[0].amount0 >= 777_000_002
    assert out[0].amount0 <= 777_000_010   # ≤ 7 wei from L=1 tick math
    assert out[0].amount1 == 0


def test_position_source_skips_read_tick_when_liquidity_zero(monkeypatch):
    """L=0 positions (fully redeemed) shouldn't trigger the two extra ticks()
    RPC reads. Verify by failing the test if read_tick is invoked."""
    from settle.extract import uniswap_v3 as v3
    from settle.normalize.sources.uniswap_v3 import RPCUniswapV3PositionSource

    AUSD = Address.from_str("0x00000000efe302beaa2b3e6e1b18d08d69a9012a")
    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    pool_addr = Address.from_str("0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d")
    owner = Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")

    pool_state = v3.V3PoolState(
        sqrt_price_x96=get_sqrt_ratio_at_tick(0), current_tick=0,
        token0=AUSD, token1=USDC, fee=100,
        fee_growth_global_0_x128=99 << 128, fee_growth_global_1_x128=99 << 128,
    )
    pos = v3.V3Position(
        token_id=1, token0=AUSD, token1=USDC, fee=100,
        tick_lower=-1, tick_upper=1, liquidity=0,
        fee_growth_inside_0_last_x128=0, fee_growth_inside_1_last_x128=0,
        tokens_owed_0=42, tokens_owed_1=0,
    )

    def boom(*a, **kw):
        raise AssertionError("read_tick must not be called for L=0 positions")

    monkeypatch.setattr(v3, "read_pool_state", lambda chain, pool, block: pool_state)
    monkeypatch.setattr(v3, "nfpm_balance_of", lambda chain, nfpm, owner, block: 1)
    monkeypatch.setattr(v3, "token_of_owner_by_index",
                        lambda chain, nfpm, owner, idx, block: 1)
    monkeypatch.setattr(v3, "read_position",
                        lambda chain, nfpm, token_id, block: pos)
    monkeypatch.setattr(v3, "read_tick", boom)

    src = RPCUniswapV3PositionSource()
    out = src.positions_in_pool(
        chain="ethereum", owner=owner.value, pool=pool_addr.value, block=1,
    )
    assert out[0].amount0 == 42
    assert out[0].amount1 == 0


# ----------------------------------------------------------------------------
# _decode_liquidity_log — IncreaseLiquidity / DecreaseLiquidity event decoding
# ----------------------------------------------------------------------------

def _hex_word(n: int) -> str:
    return hex(n)[2:].rjust(64, "0")


def _make_log(topic0: str, token_id: int, liquidity: int, amount0: int, amount1: int,
              *, block: int = 1000, log_index: int = 0):
    """Build a synthetic NFPM event log matching Alchemy's eth_getLogs shape."""
    return {
        "blockNumber": hex(block),
        "transactionHash": "0xabcd",
        "logIndex": hex(log_index),
        "topics": [topic0, "0x" + _hex_word(token_id)],
        "data": "0x" + _hex_word(liquidity) + _hex_word(amount0) + _hex_word(amount1),
    }


def test_decode_increase_liquidity_log():
    """IncreaseLiquidity → positive signed amounts."""
    log = _make_log(TOPIC_INCREASE_LIQUIDITY, token_id=42, liquidity=10**18,
                   amount0=1_000_000, amount1=2_000_000)
    ev = _decode_liquidity_log(log)
    assert ev.token_id == 42
    assert ev.amount0 == 1_000_000
    assert ev.amount1 == 2_000_000
    assert ev.is_increase is True


def test_decode_decrease_liquidity_log_signs_negative():
    """DecreaseLiquidity → negative signed amounts so they net out the prior
    deposit when summed into a daily inflow timeseries."""
    log = _make_log(TOPIC_DECREASE_LIQUIDITY, token_id=42, liquidity=10**18,
                   amount0=500_000, amount1=750_000)
    ev = _decode_liquidity_log(log)
    assert ev.amount0 == -500_000
    assert ev.amount1 == -750_000
    assert ev.is_increase is False


# ----------------------------------------------------------------------------
# DuneV3InflowSource — decodes Dune ethereum.logs rows into V3LiquidityEvent
# ----------------------------------------------------------------------------

def test_dune_v3_inflow_source_decodes_dune_rows(monkeypatch):
    """The Dune source must turn ``ethereum.logs`` row dicts into
    ``V3LiquidityEvent`` via the canonical decoder. Position discovery still
    goes through RPC; we monkeypatch those primitives.

    Regression: previously this code path didn't exist — V3 inflow events
    over a full month required eth_getLogs against an Alchemy provider that
    rate-limits at 10 blocks/request. Dune scans the whole range in one shot.
    """
    import pandas as pd

    from settle.domain.primes import Address, Chain
    from settle.extract import uniswap_v3 as v3
    from settle.normalize.sources.dune_v3_inflow import DuneV3InflowSource

    AUSD = Address.from_str("0x00000000efe302beaa2b3e6e1b18d08d69a9012a")
    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    pool_addr = Address.from_str("0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d")
    owner = Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")

    pool_state = v3.V3PoolState(
        sqrt_price_x96=get_sqrt_ratio_at_tick(0), current_tick=0,
        token0=AUSD, token1=USDC, fee=100,
        fee_growth_global_0_x128=0, fee_growth_global_1_x128=0,
    )
    pos = v3.V3Position(
        token_id=42, token0=AUSD, token1=USDC, fee=100,
        tick_lower=-1, tick_upper=1, liquidity=10**18,
        fee_growth_inside_0_last_x128=0, fee_growth_inside_1_last_x128=0,
        tokens_owed_0=0, tokens_owed_1=0,
    )

    monkeypatch.setattr(v3, "read_pool_state", lambda chain, pool, block: pool_state)
    monkeypatch.setattr(v3, "nfpm_balance_of", lambda *a, **kw: 1)
    monkeypatch.setattr(v3, "token_of_owner_by_index", lambda *a, **kw: 42)
    monkeypatch.setattr(v3, "read_position", lambda *a, **kw: pos)

    # Synthetic Dune result: one IncreaseLiquidity row.
    dune_df = pd.DataFrame([{
        "block_number": 24600000,
        "block_time": "2026-03-10",
        "tx_hash": "0xabc",
        "log_index": 7,
        "topic0": TOPIC_INCREASE_LIQUIDITY,
        "topic1": "0x" + format(42, "x").rjust(64, "0"),
        "data": "0x" + _hex_word(10**18) + _hex_word(1_000_000) + _hex_word(2_000_000),
    }])
    captured = {}

    def fake_execute_query(sql_path, params, pin_block, performance="medium"):
        captured["params"] = params
        captured["pin_block"] = pin_block
        return dune_df

    from settle.normalize.sources import dune_v3_inflow as dvi
    monkeypatch.setattr(dvi, "execute_query", fake_execute_query)

    src = DuneV3InflowSource()
    events = src.liquidity_events_in_pool(
        chain="ethereum", owner=owner.value, pool=pool_addr.value,
        from_block=24500000, to_block=24700000,
    )

    # The decoder ran — got one signed-positive event back.
    assert len(events) == 1
    assert events[0].token_id == 42
    assert events[0].amount0 == 1_000_000
    assert events[0].amount1 == 2_000_000
    assert events[0].is_increase is True

    # Dune was called with the discovered tokenId padded into the IN-list.
    assert captured["pin_block"] == 24700000
    expected_padded = "0x" + format(42, "x").rjust(64, "0")
    assert expected_padded in captured["params"]["token_ids_padded"]
    assert captured["params"]["from_block"] == 24500000


# ── Token-ID discovery + the stale-fixture guard ─────────────────────────────
#
# Grove E12, August 2026: NFT 1353600 was minted in-period. The value path
# enumerates NFPM positions live so it priced the new position, but the
# fixture's hardcoded token-ID list held only 1192575, so the matching
# IncreaseLiquidity event was absent and `revenue = eom - som - inflow` booked
# $4,000,000.00 of fresh capital as yield — 46.8% of Grove's August gross.

import pytest

from settle.domain.primes import Address, Chain
from settle.extract import uniswap_v3 as _v3


class _FakePos:
    def __init__(self, t0, t1, fee):
        self.token0, self.token1, self.fee = Address(t0), Address(t1), fee


def _patch_chain(monkeypatch, per_block, pool_tokens):
    """per_block: {block: [token_id, ...]} owned in the target pool."""
    t0, t1, fee = pool_tokens

    class _PS:
        token0, token1 = Address(t0), Address(t1)
    _PS.fee = fee
    monkeypatch.setattr(_v3, "read_pool_state", lambda c, p, b: _PS)
    monkeypatch.setattr(_v3, "nfpm_balance_of",
                        lambda c, n, o, b: len(per_block.get(b, [])))
    monkeypatch.setattr(_v3, "token_of_owner_by_index",
                        lambda c, n, o, i, b: per_block[b][i])
    monkeypatch.setattr(_v3, "read_position",
                        lambda c, n, tid, b: _FakePos(t0, t1, fee))


def test_discover_finds_positions_opened_mid_period(monkeypatch):
    """A position absent at SoM but present at EoM must still be discovered."""
    t0, t1, fee = b"\xaa" * 20, b"\xbb" * 20, 100
    _patch_chain(monkeypatch, {100: [1192575], 200: [1192575, 1353600]}, (t0, t1, fee))
    got = _v3.discover_pool_token_ids(
        Chain.ETHEREUM, Address(b"\xcc" * 20), Address(b"\xdd" * 20),
        Address(b"\xee" * 20), (100, 200),
    )
    assert got == {1192575, 1353600}


def test_discover_finds_positions_closed_mid_period(monkeypatch):
    """Present at SoM, gone by EoM — an EoM-only scan would miss it."""
    t0, t1, fee = b"\xaa" * 20, b"\xbb" * 20, 100
    _patch_chain(monkeypatch, {100: [1192575, 999], 200: [1192575]}, (t0, t1, fee))
    got = _v3.discover_pool_token_ids(
        Chain.ETHEREUM, Address(b"\xcc" * 20), Address(b"\xdd" * 20),
        Address(b"\xee" * 20), (100, 200),
    )
    assert got == {1192575, 999}


def test_discover_filters_out_other_pools(monkeypatch):
    t0, t1, fee = b"\xaa" * 20, b"\xbb" * 20, 100

    class _PS:
        token0, token1 = Address(t0), Address(t1)
    _PS.fee = fee
    monkeypatch.setattr(_v3, "read_pool_state", lambda c, p, b: _PS)
    monkeypatch.setattr(_v3, "nfpm_balance_of", lambda c, n, o, b: 2)
    monkeypatch.setattr(_v3, "token_of_owner_by_index", lambda c, n, o, i, b: [1, 2][i])
    # tokenId 2 belongs to a different pool (different fee tier).
    monkeypatch.setattr(_v3, "read_position",
                        lambda c, n, tid, b: _FakePos(t0, t1, fee if tid == 1 else 3000))
    got = _v3.discover_pool_token_ids(
        Chain.ETHEREUM, Address(b"\xcc" * 20), Address(b"\xdd" * 20),
        Address(b"\xee" * 20), (100,),
    )
    assert got == {1}


def test_discover_returns_empty_before_pool_deployment(monkeypatch):
    def _boom(c, p, b):
        raise _v3.PoolNotDeployedError("not yet")
    monkeypatch.setattr(_v3, "read_pool_state", _boom)
    assert _v3.discover_pool_token_ids(
        Chain.ETHEREUM, Address(b"\xcc" * 20), Address(b"\xdd" * 20),
        Address(b"\xee" * 20), (100, 200),
    ) == set()


# ── The guard: an in-period position with no event must stop the settlement ──

class _FakeAmounts:
    """Minimal stand-in for V3PositionAmounts."""
    def __init__(self, token_id, t0, t1):
        self.token_id = token_id
        self.token0, self.token1 = Address(t0), Address(t1)
        self.amount0 = self.amount1 = 0


class _FakeSource:
    def __init__(self, per_block, events):
        self._per_block, self._events = per_block, events

    def positions_in_pool(self, chain, owner, pool, block):
        return self._per_block[block]

    def liquidity_events_in_pool(self, chain, owner, pool, from_block, to_block):
        return self._events


def _venue_and_prime():
    from datetime import date
    from settle.domain import PricingCategory, Token, Venue
    from settle.domain.primes import Prime
    USDC = bytes.fromhex("a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    v = Venue(
        id="E12", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, Address(b"\xee" * 20), "LP", 0),
        pricing_category=PricingCategory.LP_POOL, label="v3", lp_kind="uniswap_v3",
    )
    p = Prime(id="grove", ilk_bytes32=b"\x00" * 32, start_date=date(2024, 1, 1),
              alm={Chain.ETHEREUM: Address(b"\xdd" * 20)}, venues=[v])
    return p, v, USDC


def test_position_opened_in_period_without_an_event_raises():
    """The E12 regression: new NFT priced by value, no inflow to net it."""
    from settle.normalize.positions import (
        UnsupportedPricingError, _uniswap_v3_inflow_timeseries,
    )
    prime, venue, USDC = _venue_and_prime()
    DAI = bytes.fromhex("6b175474e89094c44da98b954eedeac495271d0f")
    old = _FakeAmounts(1192575, USDC, DAI)
    new = _FakeAmounts(1353600, USDC, DAI)
    ev = _v3.V3LiquidityEvent(block_number=101, tx_hash="0x", log_index=0,
                              token_id=1192575, amount0=1, amount1=1,
                              is_increase=True)
    src = _FakeSource({100: [old], 200: [old, new]}, [ev])
    with pytest.raises(UnsupportedPricingError, match="1353600"):
        _uniswap_v3_inflow_timeseries(
            prime, venue, 100, 200, source=src, block_to_date=lambda b: None,
        )


def test_position_present_at_both_ends_needs_no_event():
    """A position that merely survived the period must NOT trip the guard."""
    from settle.normalize.positions import _uniswap_v3_inflow_timeseries
    prime, venue, USDC = _venue_and_prime()
    DAI = bytes.fromhex("6b175474e89094c44da98b954eedeac495271d0f")
    old = _FakeAmounts(1192575, USDC, DAI)
    src = _FakeSource({100: [old], 200: [old]}, [])
    out = _uniswap_v3_inflow_timeseries(
        prime, venue, 100, 200, source=src, block_to_date=lambda b: None,
    )
    assert out.empty
