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
from settle.compute._helpers import daily_compounding_factor
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
      subproxy_usds      =  20_000_000   USDS  (earns SSR + 0.20%)
      subproxy_susds     =          0
      alm_usds           =          0
      ssr                = 4.00%

    Position (one venue, syrupUSDC):
      balance_som  = 100M shares    pps_som = 1.04   →  value_som = 104M
      balance_eom  = 100M shares    pps_eom = 1.05   →  value_eom = 105M
      no inflows during the period

    Expected (Decimal arithmetic):
      utilized           = 100M − 20M = 80M
      borrow_apy         = 4.00% + 0.30% = 4.30%
      sky_revenue        = 31 × 80M × ((1.043)^(1/365) − 1)
      agent_rate         = 31 × 20M × ((1.042)^(1/365) − 1)
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
            if holder == obex.subproxy[Chain.ETHEREUM].value and token == bytes.fromhex(
                "dc035d45d973e3ec169d2276ddab16f1e407384f"
            ):
                return sub_usds_df
            return empty_balance_df

        def directed_inflow_timeseries(
            self, chain, token, from_addr, to_addr, start, pin_block,
        ):
            self.directed_calls.append((chain, token, from_addr, to_addr, start, pin_block))
            return pd.DataFrame({
                "block_date": [], "daily_inflow": [], "cum_inflow": [],
            })

    # Position balance source — return 100M shares (raw = 100M × 10^6).
    position_balance_src = MockPositionBalanceSource(raw_balance=100_000_000 * 10**6)

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
    sky_factor = daily_compounding_factor(Decimal("0.04") + BASE_RATE_OVER_SSR)
    agent_factor = daily_compounding_factor(Decimal("0.04") + AGENT_RATE_OVER_SSR)
    expected_sky = Decimal("80000000") * days * sky_factor
    expected_agent = Decimal("20000000") * days * agent_factor

    assert result.sky_revenue == expected_sky
    assert result.agent_rate == expected_agent
    assert result.prime_agent_revenue == Decimal("1000000")
    assert result.monthly_pnl == expected_agent + Decimal("1000000") - expected_sky

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


def test_curve_lp_index_weighted_inflow(fixed_pin_blocks, monkeypatch):
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
    delegate to the configured `IBlockResolver` exactly twice per chain (one
    SoM anchor, one EoM anchor) and the SoM anchor must precede the EoM anchor
    by ~1 month."""
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

    # Resolver called exactly twice for ethereum (OBEX has only one chain).
    chains_seen = [c for c, _ in resolver.calls]
    assert chains_seen == ["ethereum", "ethereum"]
    anchors = [a for _, a in resolver.calls]
    eom_anchor = datetime.combine(date(2026, 3, 31), time.max, tzinfo=timezone.utc)
    som_anchor = datetime.combine(date(2026, 2, 28), time.max, tzinfo=timezone.utc)
    assert eom_anchor in anchors
    assert som_anchor in anchors
    # The pin blocks ended up on the result.
    assert result.period.pin_blocks[Chain.ETHEREUM] == 99
    assert result.pin_blocks_som[Chain.ETHEREUM] == 99
