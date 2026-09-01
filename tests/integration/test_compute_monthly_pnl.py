"""Integration test: full ``compute_monthly_pnl`` orchestration with mock sources.

Verifies the wiring between Compute → Normalize → Sources without hitting
network. Uses a synthetic OBEX-like scenario sized for closed-form math.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from settle.compute import Sources, compute_monthly_pnl
from settle.compute._helpers import apr_daily, apy_to_apr
from settle.compute.agent_rate import AGENT_RATE_OVER_SSR
from settle.compute.sky_revenue import BASE_RATE_OVER_SSR
from settle.domain import Chain, Month
from settle.domain.config import load_prime

from ..fixtures.mock_sources import (
    MockBalanceSource,
    MockBlockResolver,
    MockConvertToAssetsSource,
    MockDebtSource,
    MockPositionBalanceSource,
    MockSSRSource,
    MockV3PositionSource,
)


@pytest.fixture
def obex(config_dir: Path):
    return load_prime(config_dir / "obex.yaml")


@pytest.fixture
def fixed_pin_blocks():
    """Skip RPC by passing both pin sets explicitly."""
    return {
        "eom": {Chain.ETHEREUM: 24971074},
        "som": {Chain.ETHEREUM: 24700000},
    }


def _zero_debt_df() -> pd.DataFrame:
    """Non-empty debt timeseries with cum_debt=0 — expresses 'no debt activity'.

    Distinct from a *missing* debt source, which `compute_sky_revenue` now
    rejects loudly to surface misconfigured Dune sources.
    """
    return pd.DataFrame({
        "block_date": [date(2025, 11, 17)],
        "daily_dart": [0.0],
        "cum_debt":   [0.0],
    })


# USDS mainnet address — the subproxy holding the agent-rate-earning balance.
_USDS = bytes.fromhex("dc035d45d973e3ec169d2276ddab16f1e407384f")


def test_monthly_pnl_zero_book_zero_pnl(obex, fixed_pin_blocks):
    """Zero balances + zero-debt timeseries → zero PnL. Sanity gate."""
    sources = Sources(
        debt=MockDebtSource(_zero_debt_df()),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({
            "effective_date": [date(2025, 12, 16)],
            "ssr_apy":        [0.04],
        })),
        position_balance=MockPositionBalanceSource(raw_balance=0),
        convert_to_assets=MockConvertToAssetsSource(raw_assets=10**6),  # pps = 1.0
    )

    result = compute_monthly_pnl(
        obex, Month(2026, 3),
        sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )

    assert result.sky_revenue == Decimal("0")
    assert result.agent_rate == Decimal("0")
    assert result.prime_agent_revenue == Decimal("0")
    assert result.monthly_pnl == Decimal("0")


def test_monthly_pnl_obex_synthetic_one_venue(obex, fixed_pin_blocks):
    """OBEX-shaped scenario, all numbers chosen for closed-form math.

    Setup (constant throughout March 2026):
      cum_debt           = 100_000_000   USDS
      subproxy_usds      =  20_000_000   USDS  (earns SSR + 0.20% via agent_rate)
      subproxy_susds     =          0
      alm_usds           =          0
      ssr                = 4.00%

    Position (one venue, syrupUSDC):
      balance_som  = 100M shares    pps_som = 1.04   →  value_som = 104M
      balance_eom  = 100M shares    pps_eom = 1.05   →  value_eom = 105M
      no inflows during the period

    Expected (Decimal arithmetic), on the NOMINAL convention adopted
    2026-09-01 — rates are summed as APRs and sliced /365, with no
    intra-period compounding (see ``apy_to_apr``, PRD §17.13):
      utilized           = 100M (subproxy USDS is treasury/risk capital — not deducted)
      ssr_apr            = 12 × ((1.04)^(1/12) − 1) = 3.928488%
      borrow_apr         = ssr_apr + 0.30% = 4.228488%
      sky_revenue        = 100M × 31/365 × borrow_apr
      agent_rate         =  20M × 31/365 × (ssr_apr + 0.20%)
      prime_revenue      = (105M − 104M) − 0 = 1M
      monthly_pnl        = prime_revenue + agent_rate − sky_revenue
    """
    # --- inputs ---
    debt_df = pd.DataFrame({
        "block_date": [date(2025, 11, 17)],
        "daily_dart": [100_000_000.0],
        "cum_debt":   [100_000_000.0],
    })
    sub_usds_df = pd.DataFrame({
        "block_date":  [date(2025, 11, 17)],
        "daily_net":   [20_000_000.0],
        "cum_balance": [20_000_000.0],
    })
    empty_balance_df = pd.DataFrame({
        "block_date": [], "daily_net": [], "cum_balance": [],
    })
    ssr_df = pd.DataFrame({
        "effective_date": [date(2025, 12, 16)],
        "ssr_apy":        [0.04],
    })

    # MockBalanceSource serves both subproxy USDS, subproxy sUSDS, ALM USDS,
    # and the directed venue inflow. We need to dispatch by holder/from_addr.
    class _SmartBalances(MockBalanceSource):
        def cumulative_balance_timeseries(
            self, chain, token, holder, start, pin_block,
        ):
            # OBEX subproxy USDS holdings — non-empty for our subproxy address only.
            self.cumulative_calls.append((chain, token, holder, start, pin_block))
            if holder == obex.subproxy[Chain.ETHEREUM].value and token == _USDS:
                return sub_usds_df
            return empty_balance_df

        def directed_inflow_timeseries(
            self, chain, token, from_addr, to_addr, start, pin_block,
        ):
            self.directed_calls.append((chain, token, from_addr, to_addr, start, pin_block))
            return pd.DataFrame({
                "block_date": [], "daily_inflow": [], "cum_inflow": [],
            })

    # Position balance source — return 100M shares (raw = 100M × 10^6) for the
    # venue token.
    #
    # It must dispatch by token, though: `normalize.balances` re-reads the
    # subproxy's USDS balance on-chain at the SoM block and re-seeds the Dune
    # series to match. A single fixed raw_balance answers that 18-decimal query
    # with the 6-decimal venue figure — 10^14 wei = 0.0001 USDS — so the anchor
    # would zero out the 20M subproxy holding and agent_rate would come back 0.
    class _BalanceByToken(MockPositionBalanceSource):
        def balance_at(self, chain, token, holder, block):
            self.calls.append((chain, token, holder, block))
            if token == _USDS:
                return 20_000_000 * 10**18
            return self.raw_balance

    position_balance_src = _BalanceByToken(raw_balance=100_000_000 * 10**6)

    # ConvertToAssets needs to differentiate SoM (pps = 1.04) vs EoM (pps = 1.05).
    class _PriceByBlock(MockConvertToAssetsSource):
        def convert_to_assets(self, chain, vault, shares, block):
            self.calls.append((chain, vault, shares, block))
            if block == fixed_pin_blocks["som"][Chain.ETHEREUM]:
                return int(Decimal("1.04") * 10**6)
            return int(Decimal("1.05") * 10**6)

    sources = Sources(
        debt=MockDebtSource(debt_df),
        balance=_SmartBalances(),
        ssr=MockSSRSource(ssr_df),
        position_balance=position_balance_src,
        convert_to_assets=_PriceByBlock(),
    )

    # --- act ---
    result = compute_monthly_pnl(
        obex, Month(2026, 3),
        sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )

    # --- assert ---
    days = 31
    # SSR is an APY; the spreads are governance APRs. Convert the first, then
    # sum, then slice — nominal, no intra-period compounding.
    ssr_apr = apy_to_apr(Decimal("0.04"))
    sky_slice = apr_daily(ssr_apr + BASE_RATE_OVER_SSR, days)
    agent_slice = apr_daily(ssr_apr + AGENT_RATE_OVER_SSR, days)
    # Utilized = full 100M debt; subproxy USDS is treasury/risk capital and is NOT
    # deducted from utilized — it earns agent_rate instead.
    expected_sky = Decimal("100000000") * sky_slice
    expected_agent = Decimal("20000000") * agent_slice

    # Tolerance, not equality: production sums 31 daily slices while the
    # closed form takes one 31-day slice. Nominal accrual makes those equal in
    # exact arithmetic, but each ``apr/365`` rounds to Decimal's 28 significant
    # digits, so 31 accumulated roundings drift ~1e-22 — femtocents. Same
    # property as ``test_apr_daily_is_plain_slicing``.
    tol = Decimal("1e-15")
    assert abs(result.sky_revenue - expected_sky) < tol
    assert abs(result.agent_rate - expected_agent) < tol
    assert result.prime_agent_revenue == Decimal("1000000")
    expected_pnl = expected_agent + Decimal("1000000") - expected_sky
    assert abs(result.monthly_pnl - expected_pnl) < tol

    # Per-venue breakdown
    assert len(result.venue_breakdown) == 1
    v = result.venue_breakdown[0]
    assert v.venue_id == "V1"
    assert v.value_som == Decimal("104000000")
    assert v.value_eom == Decimal("105000000")
    assert v.period_inflow == Decimal("0")
    assert v.revenue == Decimal("1000000")

    # Provenance — both pin sets recorded.
    assert result.pin_blocks_som == fixed_pin_blocks["som"]
    assert result.period.pin_blocks == fixed_pin_blocks["eom"]


def test_monthly_pnl_invariant_holds(obex, fixed_pin_blocks):
    """The MonthlyPnL ``__post_init__`` invariant gates round-trip math."""
    sources = Sources(
        debt=MockDebtSource(_zero_debt_df()),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({"effective_date": [date(2025, 12, 16)], "ssr_apy": [0.04]})),
        position_balance=MockPositionBalanceSource(raw_balance=0),
        convert_to_assets=MockConvertToAssetsSource(raw_assets=10**6),
    )
    result = compute_monthly_pnl(
        obex, Month(2026, 3),
        sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )
    assert result.monthly_pnl == (
        result.prime_agent_revenue + result.agent_rate - result.sky_revenue
    )


def test_v3_position_source_is_threaded_through_sources(fixed_pin_blocks):
    """``Sources.v3_position`` must reach ``_uniswap_v3_value`` — no live RPC.

    Regression: previously ``_uniswap_v3_value`` always constructed a fresh
    ``RPCUniswapV3PositionSource()`` because there was no injection point on
    ``Sources``. Tests for any prime with a V3 venue would silently hit
    Ethereum mainnet. Here we build a minimal V3-only prime and assert that
    the mock source we pass via ``Sources.v3_position`` is actually invoked.
    """
    from datetime import date as _d

    from settle.domain import Address, PricingCategory, Token
    from settle.domain.primes import Prime, Venue
    from settle.normalize.sources.uniswap_v3 import V3PositionAmounts

    AUSD = Address.from_str("0x00000000efe302beaa2b3e6e1b18d08d69a9012a")
    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    pool = Address.from_str("0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d")
    alm = Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")

    venue = Venue(
        id="E12", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, pool, "AUSDUSDC-UNI3", 0),
        pricing_category=PricingCategory.LP_POOL,
        lp_kind="uniswap_v3",
    )
    prime = Prime(
        id="grove-v3-only",
        ilk_bytes32=b"\x00" * 32,
        start_date=_d(2025, 5, 14),
        alm={Chain.ETHEREUM: alm},
        subproxy={Chain.ETHEREUM: alm},  # any address; mocks return empty timeseries
        venues=[venue],
    )

    som_block = fixed_pin_blocks["som"][Chain.ETHEREUM]
    eom_block = fixed_pin_blocks["eom"][Chain.ETHEREUM]
    v3_src = MockV3PositionSource(positions_by_block={
        som_block: [V3PositionAmounts(
            token_id=1, token0=AUSD, token1=USDC,
            amount0=12_499_500 * 10**6, amount1=12_499_500 * 10**6,
        )],
        eom_block: [V3PositionAmounts(
            token_id=1, token0=AUSD, token1=USDC,
            amount0=12_500_000 * 10**6, amount1=12_500_617 * 10**6,
        )],
    })
    sources = Sources(
        debt=MockDebtSource(_zero_debt_df()),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({
            "effective_date": [date(2025, 12, 16)], "ssr_apy": [0.04],
        })),
        # No position_balance/convert_to_assets — V3 path bypasses both.
        v3_position=v3_src,
    )

    result = compute_monthly_pnl(
        prime, Month(2026, 3),
        sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )

    # The injection point works: mock invoked exactly twice (SoM + EoM).
    assert len(v3_src.calls) == 2
    blocks_called = sorted(c[-1] for c in v3_src.calls)
    assert blocks_called == sorted([som_block, eom_block])

    # Round-trip math: SoM=$24.999M, EoM=$25.001234M → revenue = $1234.
    v = result.venue_breakdown[0]
    assert v.value_som == Decimal("24999000")
    assert v.value_eom == Decimal("25000617")
    assert v.revenue == Decimal("1617")


def test_v3_liquidity_events_net_out_inflows(fixed_pin_blocks):
    """V3 inflow tracking: ``IncreaseLiquidity`` / ``DecreaseLiquidity`` events
    must be netted out of ``revenue = (value_eom − value_som) − period_inflow``.

    Scenario: position grew from $24M → $26M during the month, but $1M of
    that came from a fresh ``IncreaseLiquidity`` deposit. True yield = $1M
    (Δvalue $2M − $1M deposit). Without inflow tracking, all $2M would count
    as revenue. With it, the deposit is netted out and revenue = $1M.
    """
    from datetime import date as _d

    from settle.domain import Address, PricingCategory, Token
    from settle.domain.primes import Prime, Venue
    from settle.extract.uniswap_v3 import V3LiquidityEvent
    from settle.normalize.sources.uniswap_v3 import V3PositionAmounts

    AUSD = Address.from_str("0x00000000efe302beaa2b3e6e1b18d08d69a9012a")
    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    pool = Address.from_str("0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d")
    alm = Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")

    venue = Venue(
        id="E12", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, pool, "AUSDUSDC-UNI3", 0),
        pricing_category=PricingCategory.LP_POOL,
        lp_kind="uniswap_v3",
    )
    prime = Prime(
        id="grove-v3-only", ilk_bytes32=b"\x00" * 32,
        start_date=_d(2025, 5, 14),
        alm={Chain.ETHEREUM: alm},
        subproxy={Chain.ETHEREUM: alm},
        venues=[venue],
    )

    som_block = fixed_pin_blocks["som"][Chain.ETHEREUM]
    eom_block = fixed_pin_blocks["eom"][Chain.ETHEREUM]
    deposit_block = (som_block + eom_block) // 2
    v3_src = MockV3PositionSource(
        positions_by_block={
            som_block: [V3PositionAmounts(
                token_id=1, token0=AUSD, token1=USDC,
                amount0=12_000_000 * 10**6, amount1=12_000_000 * 10**6,
            )],
            eom_block: [V3PositionAmounts(
                token_id=1, token0=AUSD, token1=USDC,
                amount0=13_000_000 * 10**6, amount1=13_000_000 * 10**6,
            )],
        },
        liquidity_events=[
            V3LiquidityEvent(
                block_number=deposit_block, tx_hash="0xfeed", log_index=0,
                token_id=1,
                amount0=500_000 * 10**6,    # +$500K AUSD
                amount1=500_000 * 10**6,    # +$500K USDC  → total deposit = $1M
                is_increase=True,
            ),
        ],
    )

    sources = Sources(
        debt=MockDebtSource(_zero_debt_df()),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({
            "effective_date": [date(2025, 12, 16)], "ssr_apy": [0.04],
        })),
        v3_position=v3_src,
        block_resolver=MockBlockResolver(default_date=date(2026, 3, 15)),
    )

    result = compute_monthly_pnl(
        prime, Month(2026, 3), sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )

    # The inflow path was invoked exactly once (V3 venue, single period).
    assert len(v3_src.inflow_calls) == 1
    chain, owner, pool_call, fb, tb = v3_src.inflow_calls[0]
    assert (fb, tb) == (som_block, eom_block)
    assert owner == alm.value

    # Revenue math: Δvalue = $26M − $24M = $2M; period_inflow = $1M; revenue = $1M.
    v = result.venue_breakdown[0]
    assert v.value_som == Decimal("24000000")
    assert v.value_eom == Decimal("26000000")
    assert v.period_inflow == Decimal("1000000")
    assert v.revenue == Decimal("1000000")


def test_atoken_index_weighted_inflow(fixed_pin_blocks, monkeypatch):
    """Cat C inflow tracking via scaledBalanceOf — closed-form rebase yield.

    Aave V3 aTokens rebase via a global liquidity index. The correct period
    decomposition is::

        yield         = scaledBalanceOf(SoM) × (index_eom − index_som) / RAY
        period_inflow = Δvalue − yield

    Scenario: position SoM = $100M (rebased), scaled = 95M (index = 1.05);
    EoM = $98M (rebased), scaled = 92M (index ≈ 1.0652). $5M of underlying
    was withdrawn during the period.

    yield = 95M × (1.0652 − 1.0500) = 95M × 0.0152 ≈ $1.444M (in 18-dec units).
    period_inflow = ($98M − $100M) − $1.444M = −$3.444M.
    revenue = $1.444M.
    """
    from datetime import date as _d

    from settle.domain import Address, PricingCategory, Token
    from settle.domain.primes import Prime, Venue

    RLUSD = Address.from_str("0x8292bb45bf1ee4d140127049757c2e0ff06317ed")
    aRLUSD = Address.from_str("0xfa82580c16a31d0c1bc632a36f82e83efef3eec0")
    alm = Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")

    venue = Venue(
        id="E3", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, aRLUSD, "aRLUSD", 18),
        pricing_category=PricingCategory.AAVE_ATOKEN,
        underlying=Token(Chain.ETHEREUM, RLUSD, "RLUSD", 18),
    )
    prime = Prime(
        id="grove-e3-only", ilk_bytes32=b"\x00" * 32,
        start_date=_d(2025, 5, 14),
        alm={Chain.ETHEREUM: alm},
        subproxy={Chain.ETHEREUM: alm},
        venues=[venue],
    )

    som_block = fixed_pin_blocks["som"][Chain.ETHEREUM]
    eom_block = fixed_pin_blocks["eom"][Chain.ETHEREUM]
    # 18-dec values, rebased.
    bal_som = 100 * 10**24
    bal_eom = 98 * 10**24
    # Scaled (un-rebased) values. Ratio scaled_som/scaled_eom controls yield.
    scaled_som = 95 * 10**24
    scaled_eom = 92 * 10**24

    # Patch the RPC primitives the helper calls.
    from settle.extract import rpc as _rpc

    def fake_balance_of(chain, token, holder, block):
        return bal_som if block == som_block else bal_eom

    def fake_scaled(chain, token, holder, block):
        return scaled_som if block == som_block else scaled_eom

    monkeypatch.setattr(_rpc, "balance_of", fake_balance_of)
    monkeypatch.setattr(_rpc, "scaled_balance_of", fake_scaled)

    class _ValueByBlock(MockPositionBalanceSource):
        def balance_at(self, chain, token, holder, block):
            self.calls.append((chain, token, holder, block))
            return bal_som if block == som_block else bal_eom

    sources = Sources(
        debt=MockDebtSource(_zero_debt_df()),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({
            "effective_date": [date(2025, 12, 16)], "ssr_apy": [0.04],
        })),
        position_balance=_ValueByBlock(),
    )

    result = compute_monthly_pnl(
        prime, Month(2026, 3), sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )

    v = result.venue_breakdown[0]
    assert v.value_som == Decimal("100000000")
    assert v.value_eom == Decimal("98000000")
    # yield = bal_eom × scaled_som / scaled_eom − bal_som
    #       = 98 × 95 / 92 − 100 = 101.196 − 100 = 1.196M (18-dec scaled to USD)
    expected_yield = Decimal("98000000") * Decimal("95") / Decimal("92") - Decimal("100000000")
    # period_inflow = Δvalue − yield = -$2M − $1.196M ≈ -$3.196M
    expected_inflow = Decimal("-2000000") - expected_yield
    # Decimal precision rounding tolerance: a few cents.
    assert abs(v.period_inflow - expected_inflow) < Decimal("0.01")
    assert abs(v.revenue - expected_yield) < Decimal("0.01")
    assert v.revenue > Decimal("1000000")     # ~$1.2M of yield
    assert v.revenue < Decimal("1300000")


def test_atoken_mid_period_event_stamps_inflow_on_event_date(fixed_pin_blocks, monkeypatch):
    """Per-event inflow rows must be stamped on the actual event date, NOT
    on ``period_end_date``. Regression test for the bug this PR fixes:
    previously ``_atoken_index_weighted_inflow`` returned a single row at
    period.end regardless of when the transfer happened, which inflated
    ``tw_avg_value`` (and therefore CoF allocation) for any mid-period
    withdrawal because ``_time_weighted_avg_value`` saw the position as
    held at the SoM level for the full month.

    Scenario: $100M aRLUSD held from Mar 1, withdraw $50M on Mar 10 (=
    day 10 of a 31-day period). Constant scaled balance within each
    segment → yield is zero within the segment, so the per-event inflow
    equals the raw balance delta (-$50M).

    Verifies:
      1. The returned DataFrame has 1 row (one event), block_date = Mar 10
         (NOT Mar 31 = period.end).
      2. ``tw_avg_value_usd`` reflects $100M on days 1-9 and $50M on days
         10-31 — i.e. it's between $50M and $100M, NOT $100M (the old
         single-row-at-EoM behaviour) and NOT $50M (treating the
         withdrawal as if it happened SoM).
    """
    from datetime import date as _d, timedelta as _td
    from settle.compute.prime_agent_revenue import _time_weighted_avg_value
    from settle.domain import Address, PricingCategory, Token
    from settle.domain.period import Period
    from settle.domain.primes import Prime, Venue
    from settle.normalize.positions import _atoken_index_weighted_inflow

    RLUSD = Address.from_str("0x8292bb45bf1ee4d140127049757c2e0ff06317ed")
    aRLUSD = Address.from_str("0xfa82580c16a31d0c1bc632a36f82e83efef3eec0")
    alm = Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")
    venue = Venue(
        id="E3", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, aRLUSD, "aRLUSD", 18),
        pricing_category=PricingCategory.AAVE_ATOKEN,
        underlying=Token(Chain.ETHEREUM, RLUSD, "RLUSD", 18),
    )
    prime = Prime(
        id="grove-e3-only", ilk_bytes32=b"\x00" * 32,
        start_date=_d(2025, 5, 14),
        alm={Chain.ETHEREUM: alm},
        subproxy={Chain.ETHEREUM: alm},
        venues=[venue],
    )

    som_block = fixed_pin_blocks["som"][Chain.ETHEREUM]
    eom_block = fixed_pin_blocks["eom"][Chain.ETHEREUM]
    event_pre = som_block + 1000     # block just before withdrawal
    event_post = som_block + 1001    # block at withdrawal (= "event block")
    event_date = _d(2026, 3, 10)
    bal_som = 100 * 10**24
    bal_post_event = 50 * 10**24
    bal_eom = bal_post_event
    # Constant scaled balance within each segment → zero rebase yield
    # within the segment. Two segments separated by a -$50M cash flow.
    scaled_in_segment_1 = 95 * 10**24    # pre-event
    scaled_in_segment_2 = scaled_in_segment_1 // 2   # post-event (half burned)

    def fake_balance(chain, token, holder, block):
        if block <= event_pre: return bal_som
        return bal_eom

    def fake_scaled(chain, token, holder, block):
        if block <= event_pre: return scaled_in_segment_1
        return scaled_in_segment_2

    def fake_event_blocks(chain_value, token_addr, holder_addr, som, eom):
        # Simulates the per-event boundary callback after the wrapper has
        # already converted blocks to (pre, post, date) triples.
        return [(event_pre, event_post, event_date)]

    df = _atoken_index_weighted_inflow(
        prime, venue, som_block, eom_block,
        period_end_date=_d(2026, 3, 31),
        scaled_balance_at=fake_scaled,
        balance_at=fake_balance,
        transfer_event_blocks=fake_event_blocks,
    )

    # ── Assertion 1: inflow row stamped on the actual event date ──
    assert len(df) == 1, f"expected 1 event row, got {len(df)}: {df!r}"
    assert df.iloc[0]["block_date"] == event_date, (
        f"event row stamped on {df.iloc[0]['block_date']} "
        f"(expected {event_date}); the pre-PR bug was to use period_end_date"
    )
    # ── Assertion 2: inflow ≈ -$50M (the burn delta, with zero in-segment yield) ──
    assert df.iloc[0]["daily_inflow"] < Decimal("-49_000_000")
    assert df.iloc[0]["daily_inflow"] > Decimal("-51_000_000")

    # ── Assertion 3: tw_avg_value sees the position drop on the right day ──
    period = Period(start=_d(2026, 3, 1), end=_d(2026, 3, 31),
                    pin_blocks=fixed_pin_blocks["eom"])
    tw_avg = _time_weighted_avg_value(period, Decimal("100_000_000"), df)
    # 9 days × $100M + 22 days × $50M ≈ ($900M + $1100M) / 31 ≈ $64.5M.
    # If event were stamped at period.end (old bug): tw_avg ≈ $100M.
    # If stamped at period.start: tw_avg ≈ $50M. Anything between confirms
    # the per-event date stamping is working.
    assert Decimal("60_000_000") < tw_avg < Decimal("70_000_000"), (
        f"tw_avg={tw_avg} — should be ~$64.5M reflecting Mar 10 event date; "
        f"$100M means event was treated as EoM (pre-PR bug); "
        f"$50M means event was treated as SoM"
    )


def test_atoken_clean_exit_binary_searches_withdrawal_block(fixed_pin_blocks, monkeypatch):
    """Mid-period full withdrawal — partial-period yield via binary search.

    The closed-form ``bal_eom × scaled_som / scaled_eom − bal_som`` degenerates
    when ``scaled_eom`` collapses to dust (Aave V3 leaves 1 wei on full exit):
    the ratio becomes ≈1, the formula simplifies to ``scaled_som − bal_som``,
    and we get a phantom *negative* equal to the pre-period yield already
    embedded in ``bal_som``. For E2 aHorRwaUSDC Feb 2026 (bal_som $11.6M,
    index_som ≈1.020), that's a phantom −$232K with zero economic basis.

    Fix: when ``scaled_eom < 0.1% × scaled_som``, binary-search on
    ``scaled_balance_at`` to find the withdrawal block ``W`` (first block
    where scaled drops to dust), then read ``balance_at(W − 1)`` to get
    the rebased pre-withdrawal balance — the on-chain rebase has already
    folded the correct ``index_W`` into that read. Yield = bal_pre − bal_som.

    Aave's standard ``Transfer`` event emits the *scaled* amount (per its
    ``_burn`` / ``_mint`` override), so the events-sum path used by Cat A
    would NOT reconcile against rebased boundary balances — that's why we
    use balance reads, not event sums.

    Scenario: $11.6M aHorRwaUSDC (6-dec), withdrawn at block ``W`` with
    bal_W ≈ $11.62M (partial-period yield $20K already accrued); after
    withdrawal scaled balance → 1 wei dust. Expected: revenue = $20K.
    """
    from datetime import date as _d
    from settle.domain import Address, PricingCategory, Token
    from settle.domain.primes import Prime, Venue

    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    aHorRwaUSDC = Address.from_str("0x68215b6533c47ff9f7125ac95adf00fe4a62f79e")
    alm = Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")

    venue = Venue(
        id="E2", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, aHorRwaUSDC, "aHorRwaUSDC", 6),
        pricing_category=PricingCategory.AAVE_ATOKEN,
        underlying=Token(Chain.ETHEREUM, USDC, "USDC", 6),
    )
    prime = Prime(
        id="grove-e2-only", ilk_bytes32=b"\x00" * 32,
        start_date=_d(2025, 5, 14),
        alm={Chain.ETHEREUM: alm},
        subproxy={Chain.ETHEREUM: alm},
        venues=[venue],
    )

    som_block = fixed_pin_blocks["som"][Chain.ETHEREUM]
    eom_block = fixed_pin_blocks["eom"][Chain.ETHEREUM]
    # Pin a synthetic withdrawal block between SoM and EoM. The binary
    # search must converge to this block.
    withdrawal_block = (som_block + eom_block) // 2

    # On-chain shape: pre-period yield embedded (bal_som > scaled_som,
    # index_som ≈ 1.020), small partial-period yield earned to W,
    # then dust after withdrawal.
    bal_som     = 11_600_000 * 10**6     # $11.6M aHorRwaUSDC (rebased at SoM)
    scaled_som  = 11_372_549 * 10**6     # ≈ bal_som / 1.020
    bal_pre_W   = 11_620_000 * 10**6     # bal at W-1: $11.62M (+$20K accrued)
    bal_dust    = 1                      # 1 wei × index_eom/RAY ≈ 1
    scaled_dust = 1

    # Stub: scaled_balance is scaled_som while pre-withdrawal, dust after.
    def fake_scaled(c, t, h, b):
        if b < withdrawal_block:
            return scaled_som
        return scaled_dust

    # Stub: balance is bal_som at som_block; bal_pre_W at the block right
    # before the withdrawal; dust at eom and after.
    def fake_balance(c, t, h, b):
        if b == som_block:
            return bal_som
        if b == withdrawal_block - 1:
            return bal_pre_W
        if b >= withdrawal_block:
            return bal_dust
        # In-between blocks before withdrawal: extrapolate linearly between
        # bal_som and bal_pre_W (irrelevant to the test outcome but keeps
        # the stub well-defined).
        return bal_som + (bal_pre_W - bal_som) * (b - som_block) // (withdrawal_block - 1 - som_block)

    from settle.extract import rpc as _rpc
    monkeypatch.setattr(_rpc, "balance_of", fake_balance)
    monkeypatch.setattr(_rpc, "scaled_balance_of", fake_scaled)

    class _ValueByBlock(MockPositionBalanceSource):
        def balance_at(self, chain, token, holder, block):
            self.calls.append((chain, token, holder, block))
            return fake_balance(chain, token, holder, block)

    sources = Sources(
        debt=MockDebtSource(_zero_debt_df()),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({
            "effective_date": [date(2025, 12, 16)], "ssr_apy": [0.04],
        })),
        position_balance=_ValueByBlock(),
    )

    result = compute_monthly_pnl(
        prime, Month(2026, 3), sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )

    v = result.venue_breakdown[0]
    # value_som = $11.6M, value_eom ≈ $0 (1 wei dust).
    assert v.value_som == Decimal("11600000")
    assert v.value_eom < Decimal("0.01")
    # revenue = bal_pre_W − bal_som = +$20K (the real partial-period yield).
    # NOT the phantom −$232K the closed-form would have produced.
    assert abs(v.revenue - Decimal("20000")) < Decimal("0.01")
    assert v.revenue > Decimal("0")


def test_atoken_multi_withdrawal_falls_back_to_zero_yield(fixed_pin_blocks, monkeypatch):
    """Multi-withdrawal sentinel — when the position is drained in stages
    rather than a single burn, the binary-search-for-W approach lands on
    the LAST burn block where ``bal_pre_W`` is just a residual, producing
    a large *negative* phantom (E2 Feb 2026 saw $11.37M → $6.48M → 0 in
    two withdrawals; binary search alone gave −$4.98M revenue).

    We detect multi-withdrawal by reading scaled_balance at the period
    midpoint *before* the search: if scaled is neither ≈ scaled_som nor
    ≈ dust, the position is being drained in stages and we fall back to
    yield = 0 (same conservative behaviour as the dust guard). Properly
    attributing yield across multi-segment withdrawals requires per-event
    index reads — deferred. The lost yield (~$20K/mo for Horizon-sized
    positions) is the acceptable cost of refusing to publish nonsense.

    Scenario mirroring real E2 Feb 2026: scaled drops from $11.37M to
    $6.48M (block ~W1, mid-period), then to dust (block ~W2 in the
    second half). Midpoint read shows scaled = $6.48M (~57% of scaled_som,
    intermediate). Expected: revenue = $0 (fallback), period_inflow =
    delta_value = −$11.6M (all classified as capital out).
    """
    from datetime import date as _d
    from settle.domain import Address, PricingCategory, Token
    from settle.domain.primes import Prime, Venue

    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    aHorRwaUSDC = Address.from_str("0x68215b6533c47ff9f7125ac95adf00fe4a62f79e")
    alm = Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")

    venue = Venue(
        id="E2", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, aHorRwaUSDC, "aHorRwaUSDC", 6),
        pricing_category=PricingCategory.AAVE_ATOKEN,
        underlying=Token(Chain.ETHEREUM, USDC, "USDC", 6),
    )
    prime = Prime(
        id="grove-e2-only", ilk_bytes32=b"\x00" * 32,
        start_date=_d(2025, 5, 14),
        alm={Chain.ETHEREUM: alm},
        subproxy={Chain.ETHEREUM: alm},
        venues=[venue],
    )

    som_block = fixed_pin_blocks["som"][Chain.ETHEREUM]
    eom_block = fixed_pin_blocks["eom"][Chain.ETHEREUM]
    mid_block = (som_block + eom_block) // 2

    bal_som     = 11_600_000 * 10**6
    scaled_som  = 11_372_549 * 10**6
    # First withdrawal at W1 (mid-period); intermediate balance afterward.
    scaled_mid  = 6_480_000 * 10**6     # ~57% of scaled_som
    # Final withdrawal in second half; dust at EoM.
    bal_dust, scaled_dust = 1, 1

    def fake_scaled(c, t, h, b):
        if b == som_block:
            return scaled_som
        if b == mid_block:
            return scaled_mid                 # intermediate — multi-withdrawal sentinel
        if b == eom_block:
            return scaled_dust
        # Other blocks irrelevant to the test outcome (sentinel triggers
        # before any binary-search read).
        return scaled_dust

    monkeypatch.setattr("settle.extract.rpc.balance_of",
                        lambda c, t, h, b: bal_som if b == som_block else bal_dust)
    monkeypatch.setattr("settle.extract.rpc.scaled_balance_of", fake_scaled)

    class _ValueByBlock(MockPositionBalanceSource):
        def balance_at(self, chain, token, holder, block):
            self.calls.append((chain, token, holder, block))
            return bal_som if block == som_block else bal_dust

    sources = Sources(
        debt=MockDebtSource(_zero_debt_df()),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({
            "effective_date": [date(2025, 12, 16)], "ssr_apy": [0.04],
        })),
        position_balance=_ValueByBlock(),
    )

    result = compute_monthly_pnl(
        prime, Month(2026, 3), sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )

    v = result.venue_breakdown[0]
    # value_som = $11.6M, value_eom ≈ $0.
    assert v.value_som == Decimal("11600000")
    assert v.value_eom < Decimal("0.01")
    # Fallback: yield = 0 → revenue = 0. period_inflow = full delta_value
    # (= all classified as capital, no yield credited).
    assert v.revenue == Decimal("0") or abs(v.revenue) < Decimal("0.01")
    assert abs(v.period_inflow - (v.value_eom - v.value_som)) < Decimal("0.01")


def test_erc4626_closed_form_inflow_for_non_dune_chain(fixed_pin_blocks, monkeypatch):
    """Cat B closed-form path on Monad — uses only ``balanceOf`` +
    ``convertToAssets`` at SoM/EoM (no event scanning, since Monad's public
    RPC limits ``eth_getLogs`` to 100-block windows).

    Mirrors the math of ``_atoken_index_weighted_inflow`` for ERC-4626::

        yield         = shares_som × (pps_eom − pps_som)
        period_inflow = (shares_eom − shares_som) × pps_eom

    Scenario: 25M shares @ pps $1.00188 SoM → 10.24M shares @ pps $1.00333
    EoM (the real Grove Monad position for Feb 2026 — partial bridge to
    Ethereum). Expected: ``actual_revenue ≈ +$36K`` of legitimate
    monthly yield, ``period_inflow ≈ −$14.81M`` of principal outflow.
    Grove's spreadsheet books this as a phantom −$14.7M "loss" because
    their methodology doesn't classify cross-chain transfers as capital
    — we explicitly do.
    """
    from decimal import Decimal as _D
    from datetime import date as _d
    from settle.domain import Address, PricingCategory, Token
    from settle.domain.primes import Prime, Venue

    AUSD = Address.from_str("0x00000000efe302beaa2b3e6e1b18d08d69a9012a")
    VAULT = Address.from_str("0x32841a8511d5c2c5b253f45668780b99139e476d")
    alm = Address.from_str("0x94b398acb2fce988871218221ea6a4a2b26cccbc")

    # 18-dec vault token, 6-dec AUSD underlying.
    venue = Venue(
        id="E25", chain=Chain.MONAD,
        token=Token(Chain.MONAD, VAULT, "grove-bbqAUSD-mon", 18),
        pricing_category=PricingCategory.ERC4626_VAULT,
        underlying=Token(Chain.MONAD, AUSD, "AUSD", 6),
    )
    # The orchestrator always reads the Ethereum debt timeseries regardless
    # of where venues live — include an Ethereum ALM entry so that part of
    # ``compute_monthly_pnl`` runs cleanly. The venue itself is Monad-only.
    eth_alm = Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")
    prime = Prime(
        id="grove-mon-only", ilk_bytes32=b"\x00" * 32,
        start_date=_d(2025, 5, 14),
        alm={Chain.MONAD: alm, Chain.ETHEREUM: eth_alm},
        subproxy={Chain.ETHEREUM: eth_alm},
        venues=[venue],
    )

    SOM_BLOCK = 52_416_879   # Monad block at Jan 31 EoD UTC
    EOM_BLOCK = 58_446_079   # Monad block at Feb 28 EoD UTC
    SHARES_SOM_RAW = 24_999_431_830_772_855_197_030_144   # 25.00M × 10^18
    SHARES_EOM_RAW = 10_236_654_652_928_429_901_551_536   # 10.24M × 10^18
    PPS_SOM_AUSD_RAW = 1_001_879                          # $1.00188 (AUSD 6-dec)
    PPS_EOM_AUSD_RAW = 1_003_330                          # $1.00333

    # Stub balanceOf + convertToAssets at the RPC layer. Avoid involving
    # ``fixed_pin_blocks`` for Monad — the fixture's blocks are Ethereum
    # block numbers; here we override pin_blocks_som / pin_blocks_eom for
    # Monad specifically.
    from settle.extract import rpc as _rpc

    def fake_balance_of(chain, token, holder, block):
        return SHARES_SOM_RAW if block == SOM_BLOCK else SHARES_EOM_RAW

    def fake_convert(chain, vault, shares, block):
        # convertToAssets(1e18) returns AUSD-raw at this pps.
        pps_raw = PPS_SOM_AUSD_RAW if block == SOM_BLOCK else PPS_EOM_AUSD_RAW
        return shares * pps_raw // (10**18)

    monkeypatch.setattr(_rpc, "balance_of", fake_balance_of)
    monkeypatch.setattr(_rpc, "convert_to_assets", fake_convert)

    class _RPCBal(MockPositionBalanceSource):
        def balance_at(self, chain, token, holder, block):
            self.calls.append((chain, token, holder, block))
            return fake_balance_of(chain, token, holder, block)

    class _RPC4626(MockConvertToAssetsSource):
        def convert_to_assets(self, chain, vault, shares, block):
            return fake_convert(chain, vault, shares, block)

    sources = Sources(
        debt=MockDebtSource(_zero_debt_df()),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({
            "effective_date": [date(2025, 12, 16)], "ssr_apy": [0.04],
        })),
        position_balance=_RPCBal(),
        convert_to_assets=_RPC4626(),
    )

    result = compute_monthly_pnl(
        prime, Month(2026, 3), sources=sources,
        pin_blocks_eom={
            Chain.MONAD: EOM_BLOCK,
            Chain.ETHEREUM: fixed_pin_blocks["eom"][Chain.ETHEREUM],
        },
        pin_blocks_som={
            Chain.MONAD: SOM_BLOCK,
            Chain.ETHEREUM: fixed_pin_blocks["som"][Chain.ETHEREUM],
        },
    )

    v = result.venue_breakdown[0]
    # value_som = 25M × $1.00188 ≈ $25,046,406
    # value_eom = 10.24M × $1.00333 ≈ $10,270,743
    assert abs(v.value_som - _D("25046405.76")) < _D("1")
    assert abs(v.value_eom - _D("10270742.71")) < _D("1")
    # yield = shares_som × (pps_eom − pps_som) = 25M × $0.00145 ≈ $36.3K
    # period_inflow = Δvalue − yield = -$14.78M - $36.3K ≈ -$14.81M
    # revenue = yield (Cat B is non-SDE for E25)
    assert abs(v.revenue - _D("36275")) < _D("100")
    assert v.period_inflow < _D("-14000000")  # large principal outflow
    assert v.period_inflow > _D("-15000000")
    """Curve LP inflow via closed-form ``balance × unit_price`` (analogous to
    Aave's scaledBalance × index). Avoids decoding the diverse Curve event
    signatures (NextGen vs. Plain Pool vs. Vyper variants).

    Scenario: ALM held 24M LP tokens at SoM (unit_price = $1.000) and 25M LP
    tokens at EoM (unit_price = $1.001 — 0.1% virtual_price drift = pool fees).
    Δvalue = $25.025M − $24M = $1.025M.
    yield = balance_som × Δprice = 24M × 0.001 = $24K.
    period_inflow = Δvalue − yield = $1.001M (the new $1M of LP at EoM price).
    revenue = $24K (the fee accrual).
    """
    from datetime import date as _d

    from settle.domain import Address, PricingCategory, Token
    from settle.domain.primes import Prime, Venue

    AUSD = Address.from_str("0x00000000efe302beaa2b3e6e1b18d08d69a9012a")
    USDC = Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    pool = Address.from_str("0xe79c1c7e24755574438a26d5e062ad2626c04662")
    alm = Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e")

    venue = Venue(
        id="E11", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, pool, "AUSDUSDC-CRV", 18),
        pricing_category=PricingCategory.LP_POOL,
        lp_kind="curve_stableswap",
    )
    prime = Prime(
        id="grove-curve-only", ilk_bytes32=b"\x00" * 32,
        start_date=_d(2025, 5, 14),
        alm={Chain.ETHEREUM: alm},
        subproxy={Chain.ETHEREUM: alm},
        venues=[venue],
    )

    som_block = fixed_pin_blocks["som"][Chain.ETHEREUM]
    eom_block = fixed_pin_blocks["eom"][Chain.ETHEREUM]

    class _MockCurvePool:
        def __init__(self):
            self.read_calls = []

        def read_pool(self, chain, pool_address, block):
            from settle.normalize.sources.curve_pool import CurvePoolState
            self.read_calls.append((chain, pool_address, block))
            # Reserves grow by 0.1% from SoM to EoM (pool fees accrue), but
            # total_supply stays roughly constant — boosts unit_price.
            if block == som_block:
                # 24M total reserves, 24M LP supply → unit_price = $1.000
                return CurvePoolState(
                    virtual_price_raw=10**18, total_supply=24_000_000 * 10**18,
                    coins=[AUSD, USDC],
                    balances=[12_000_000 * 10**6, 12_000_000 * 10**6],
                )
            # EoM: 25.025M reserves, 25M LP supply → unit_price = $1.001
            return CurvePoolState(
                virtual_price_raw=10**18, total_supply=25_000_000 * 10**18,
                coins=[AUSD, USDC],
                balances=[12_512_500 * 10**6, 12_512_500 * 10**6],
            )

    curve_src = _MockCurvePool()

    # Patch balance_of to return 24M LP at SoM, 25M LP at EoM.
    from settle.extract import rpc as _rpc

    def fake_balance_of(chain, token, holder, block):
        return 24_000_000 * 10**18 if block == som_block else 25_000_000 * 10**18

    monkeypatch.setattr(_rpc, "balance_of", fake_balance_of)

    sources = Sources(
        debt=MockDebtSource(_zero_debt_df()),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({
            "effective_date": [date(2025, 12, 16)], "ssr_apy": [0.04],
        })),
        position_balance=MockPositionBalanceSource(raw_balance=24_000_000 * 10**18),
        curve_pool=curve_src,
        block_resolver=MockBlockResolver(default_date=date(2026, 3, 15)),
    )

    result = compute_monthly_pnl(
        prime, Month(2026, 3), sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )

    v = result.venue_breakdown[0]
    # value_som = 24M LP × $1.000 = $24M.
    assert v.value_som == Decimal("24000000")
    # value_eom uses the EoM RPC balance × EoM unit_price = 24M × 1.001 = $24.024M
    # (the value path reads balance via position_balance source, not RPC fake).
    assert abs(v.value_eom - Decimal("24024024")) < Decimal("100")
    # period_inflow = (Δbalance) × unit_price_eom = 1M × 1.001 = $1.001M.
    assert abs(v.period_inflow - Decimal("1001000.998")) < Decimal("100")
    # revenue ≈ value_eom − value_som − period_inflow ≈ $0 (yield - inflow netting)


def test_monthly_pnl_invokes_block_resolver_for_both_som_and_eom(obex):
    """When pin_blocks_eom/som are not supplied, `compute_monthly_pnl` must
    delegate to the configured `IBlockResolver` for both the SoM and the EoM
    anchor, and the SoM anchor must precede the EoM anchor by ~1 month.

    Not an exact call count: `get_debt_timeseries`'s daily expansion resolves
    one EoD block per calendar day as well, so a March run makes 31 further
    ethereum calls on top of the two pin anchors."""
    from datetime import datetime, time, timedelta, timezone

    from ..fixtures.mock_sources import MockBlockResolver

    resolver = MockBlockResolver(default=99)  # ALM uses this for any unknown anchor
    sources = Sources(
        debt=MockDebtSource(_zero_debt_df()),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({"effective_date": [date(2025, 1, 1)], "ssr_apy": [0.04]})),
        position_balance=MockPositionBalanceSource(raw_balance=0),
        convert_to_assets=MockConvertToAssetsSource(raw_assets=10**6),
        block_resolver=resolver,
    )

    result = compute_monthly_pnl(
        obex, Month(2026, 3), sources=sources,
        # Both pin sets None → resolver must be invoked
    )

    # Every call is for ethereum — OBEX has only one chain.
    chains_seen = {c for c, _ in resolver.calls}
    assert chains_seen == {"ethereum"}
    anchors = [a for _, a in resolver.calls]
    eom_anchor = datetime.combine(date(2026, 3, 31), time.max, tzinfo=timezone.utc)
    som_anchor = datetime.combine(date(2026, 2, 28), time.max, tzinfo=timezone.utc)
    assert eom_anchor in anchors
    assert som_anchor in anchors
    # SoM is resolved as its own anchor, not merely as a by-product of the
    # daily expansion — that only covers 2026-03-01..03-31.
    assert som_anchor < datetime.combine(date(2026, 3, 1), time.max, tzinfo=timezone.utc)
    # The pin blocks ended up on the result.
    assert result.period.pin_blocks[Chain.ETHEREUM] == 99
    assert result.pin_blocks_som[Chain.ETHEREUM] == 99
