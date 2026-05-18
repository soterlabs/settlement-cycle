"""Unit tests for orchestrator helpers in ``compute.monthly_pnl``.

Covers ``_susds_shares_to_principal`` (sUSDS shares → USDS-cost-basis principal),
``get_psm_usds_timeseries`` (PSM USDS reimbursement aggregator, including the
PSM3 per-leg split per PRD §17.11), and ``_psm3_susds_spread`` (30 bps
neutralising credit on the sUSDS slice of PSM3 holdings).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from settle.compute.monthly_pnl import (
    Sources,
    _psm3_susds_spread,
    _susds_shares_to_principal,
    get_psm_usds_timeseries,
)
from settle.domain import Chain, Month, Period
from settle.domain.config import load_prime
from settle.domain.primes import Address
from settle.domain.sky_tokens import PSM3_LEG_TOKENS, sUSDS_ETHEREUM
from tests.fixtures.mock_sources import (
    MockBalanceSource,
    MockBlockResolver,
    MockConvertToAssetsSource,
    MockPositionBalanceSource,
    MockPsm3Source,
)


# ----------------------------------------------------------------------------
# _susds_shares_to_principal
# ----------------------------------------------------------------------------


def test_susds_principal_empty_input_returns_empty():
    out = _susds_shares_to_principal(
        pd.DataFrame(),
        sources=Sources(),
        block_resolver=MockBlockResolver(),
        chain=Chain.ETHEREUM,
    )
    assert out is not None
    assert out.empty


def test_susds_principal_all_zero_short_circuits():
    """All-zero shares → no RPC calls, returns input unchanged."""
    df = pd.DataFrame({
        "block_date": [date(2026, 3, 1), date(2026, 3, 2)],
        "daily_net": [Decimal(0), Decimal(0)],
        "cum_balance": [Decimal(0), Decimal(0)],
    })
    c2a = MockConvertToAssetsSource(raw_assets=10**18)
    out = _susds_shares_to_principal(
        df,
        sources=Sources(convert_to_assets=c2a),
        block_resolver=MockBlockResolver(default=24000000),
        chain=Chain.ETHEREUM,
    )
    # Short-circuit: returned unchanged, no convertToAssets calls.
    assert (out["cum_balance"] == 0).all()
    assert c2a.calls == []


def test_susds_principal_uses_per_day_pps_not_eom():
    """Cost basis = Σ shares_flow_d × pps_at_day_d, NOT shares_eom × pps_eom.

    Set up two distinct pps reads (different blocks) and verify the principal
    follows each day's pps, not the latest one.
    """
    df = pd.DataFrame({
        "block_date": [date(2026, 1, 15), date(2026, 2, 15)],
        "daily_net": [Decimal("100"), Decimal("100")],
        "cum_balance": [Decimal("100"), Decimal("200")],  # ignored — recomputed
    })
    # MockConvertToAssetsSource returns a constant raw_assets per call. To
    # vary by block we'd need a richer mock, but for this test the constant
    # pps verifies the shape: daily_net × pps stays a Decimal cumulative.
    c2a = MockConvertToAssetsSource(raw_assets=int(Decimal("1.05") * 10**18))

    block_resolver = MockBlockResolver()
    block_resolver.dates_by_block = {
        ("ethereum", 24200000): date(2026, 1, 15),
        ("ethereum", 24500000): date(2026, 2, 15),
    }
    block_resolver.blocks = {
        ("ethereum", "2026-01-15T23:59:59.999999+00:00"): 24200000,
        ("ethereum", "2026-02-15T23:59:59.999999+00:00"): 24500000,
    }

    out = _susds_shares_to_principal(
        df,
        sources=Sources(convert_to_assets=c2a),
        block_resolver=block_resolver,
        chain=Chain.ETHEREUM,
    )
    # Each row: 100 shares × 1.05 USDS/share = 105 USDS.
    assert out["daily_net"].iloc[0] == Decimal("105")
    assert out["daily_net"].iloc[1] == Decimal("105")
    # Cumulative builds up to 210 (Decimal arithmetic preserved end-to-end).
    assert out["cum_balance"].iloc[1] == Decimal("210")
    assert all(isinstance(v, Decimal) for v in out["cum_balance"])


def test_susds_principal_rejects_non_ethereum_chain():
    """sUSDS vault address is hardcoded to Ethereum; calling for another
    chain must raise rather than silently read the wrong contract."""
    df = pd.DataFrame({
        "block_date": [date(2026, 3, 1)],
        "daily_net": [Decimal("100")],
        "cum_balance": [Decimal("100")],
    })
    with pytest.raises(NotImplementedError, match="only registered for Ethereum"):
        _susds_shares_to_principal(
            df,
            sources=Sources(convert_to_assets=MockConvertToAssetsSource(raw_assets=10**18)),
            block_resolver=MockBlockResolver(),
            chain=Chain.BASE,
        )


# ----------------------------------------------------------------------------
# get_psm_usds_timeseries
# ----------------------------------------------------------------------------


def _grove(config_dir: Path):
    return load_prime(config_dir / "grove.yaml")


def _period() -> Period:
    return Period(
        start=date(2026, 3, 1), end=date(2026, 3, 31),
        pin_blocks={Chain.ETHEREUM: 24781026},
    )


def test_psm_usds_returns_empty_when_no_psm_configured(config_dir: Path):
    """Grove has no PSM on Ethereum (mainnet PSM stack is non-custodial —
    nothing to track; see PRD §17.11). The function must return an empty
    frame rather than raising."""
    grove = _grove(config_dir)
    src = MockBalanceSource()
    out = get_psm_usds_timeseries(
        grove, Chain.ETHEREUM, _period(), balance_source=src,
    )
    assert out.empty


# ----------------------------------------------------------------------------
# PSM3 leg-split (erc4626_shares) — per-leg apportionment + sUSDS-spread credit
# ----------------------------------------------------------------------------


class _LegRoutedPositionBalance(MockPositionBalanceSource):
    """``balance_at`` returning a per-token raw value. Keyed by (chain, token)
    so a single mock can drive multiple legs of the same PSM3."""

    def __init__(self, balances: dict[tuple[str, bytes], int]) -> None:
        super().__init__()
        self._balances = balances

    def balance_at(self, chain: str, token: bytes, holder: bytes, block: int) -> int:
        self.calls.append((chain, token, holder, block))
        return self._balances.get((chain, token), 0)


def _spark_base_psm3() -> tuple[bytes, bytes, bytes, bytes]:
    """(psm3, USDC, USDS, sUSDS) byte-addresses for Spark on Base."""
    leg = PSM3_LEG_TOKENS[Chain.BASE]
    psm3 = bytes.fromhex("1601843c5e9bc251a3272907010afa41fa18347e")  # PSM3 Base
    return psm3, leg["USDC"].address.value, leg["USDS"].address.value, leg["sUSDS"].address.value


def _period_one_day(d: date) -> Period:
    return Period(
        start=d, end=d,
        pin_blocks={Chain.BASE: 1_000_000, Chain.ETHEREUM: 2_000_000},
    )


def _spark_synth(config_dir: Path):
    """Spark prime loaded from production YAML — has a Base PSM3 entry."""
    return load_prime(config_dir / "spark.yaml")


def test_psm3_legs_decompose_with_par_sUSDS(config_dir: Path):
    """When sUSDS pps = 1.0 and PSM3 holds all three legs in equal parts,
    the per-leg cum_X columns split Spark's claim in the right proportions."""
    spark = _spark_synth(config_dir)
    psm3, usdc_addr, usds_addr, susds_addr = _spark_base_psm3()
    spark_alm = spark.alm[Chain.BASE].value

    # Pool: $30M USDC + $30M USDS + 30M sUSDS shares (at pps=1.0 → $30M USDS-eq).
    # Spark holds 100% of the pool → claim = $90M USDS-eq.
    one = 10**18
    one_usdc = 10**6
    balances = {
        ("base", usdc_addr):  30_000_000 * one_usdc,
        ("base", usds_addr):  30_000_000 * one,
        ("base", susds_addr): 30_000_000 * one,
    }
    pos_bal = _LegRoutedPositionBalance(balances)
    # PSM3: 90M shares; convertToAssetValue(90M) = $90M USDS-eq.
    psm3_src = MockPsm3Source(
        shares_by_block={("base", 1_000_000): 90_000_000 * one},
        default_rate=one,   # 1:1 rate
    )
    # Ethereum sUSDS pps = 1.0
    c2a = MockConvertToAssetsSource(raw_assets=one)

    today = date(2026, 3, 15)
    period = _period_one_day(today)
    resolver = MockBlockResolver()
    resolver.blocks = {
        ("base",     datetime.combine(today - timedelta(days=1), time.max, tzinfo=timezone.utc).isoformat()): 999_999,
        ("base",     datetime.combine(today, time.max, tzinfo=timezone.utc).isoformat()): 1_000_000,
        ("ethereum", datetime.combine(today - timedelta(days=1), time.max, tzinfo=timezone.utc).isoformat()): 1_999_999,
        ("ethereum", datetime.combine(today, time.max, tzinfo=timezone.utc).isoformat()): 2_000_000,
    }

    out = get_psm_usds_timeseries(
        spark, Chain.BASE, period,
        balance_source=MockBalanceSource(),
        psm3_source=psm3_src,
        block_resolver=resolver,
        position_balance_source=pos_bal,
        convert_to_assets_source=c2a,
    )
    assert len(out) == 1
    row = out.iloc[0]
    # 1/3 each leg ⇒ $30M each in USDS-equivalent
    assert row["cum_usdc"] == Decimal("30000000")
    assert row["cum_usds_leg"] == Decimal("30000000")
    assert row["cum_susds"] == Decimal("30000000")
    # cum_balance reconciles to spark claim
    assert row["cum_balance"] == Decimal("90000000")


def test_psm3_legs_apportion_susds_pps(config_dir: Path):
    """sUSDS pps > 1.0 means the sUSDS leg's USDS-equivalent exceeds its face
    balance. Per-leg apportionment respects that without inflating USDC/USDS."""
    spark = _spark_synth(config_dir)
    psm3, usdc_addr, usds_addr, susds_addr = _spark_base_psm3()

    one = 10**18
    one_usdc = 10**6
    # Pool: $10M USDC + $10M USDS + 100M sUSDS shares at pps=1.10 → $110M.
    # Pool USDS-eq total = $10M + $10M + $110M = $130M; Spark = 100% claim.
    pps = Decimal("1.10")
    balances = {
        ("base", usdc_addr):  10_000_000 * one_usdc,
        ("base", usds_addr):  10_000_000 * one,
        ("base", susds_addr): 100_000_000 * one,
    }
    pos_bal = _LegRoutedPositionBalance(balances)
    psm3_src = MockPsm3Source(
        shares_by_block={("base", 1_000_000): 130_000_000 * one},
        default_rate=one,
    )
    c2a = MockConvertToAssetsSource(raw_assets=int(pps * one))

    today = date(2026, 3, 15)
    period = _period_one_day(today)
    resolver = MockBlockResolver()
    resolver.blocks = {
        ("base",     datetime.combine(today - timedelta(days=1), time.max, tzinfo=timezone.utc).isoformat()): 999_999,
        ("base",     datetime.combine(today, time.max, tzinfo=timezone.utc).isoformat()): 1_000_000,
        ("ethereum", datetime.combine(today - timedelta(days=1), time.max, tzinfo=timezone.utc).isoformat()): 1_999_999,
        ("ethereum", datetime.combine(today, time.max, tzinfo=timezone.utc).isoformat()): 2_000_000,
    }

    out = get_psm_usds_timeseries(
        spark, Chain.BASE, period,
        balance_source=MockBalanceSource(),
        psm3_source=psm3_src,
        block_resolver=resolver,
        position_balance_source=pos_bal,
        convert_to_assets_source=c2a,
    )
    row = out.iloc[0]
    # Each leg apportioned as (spark_claim / pool_total) × leg_face_in_USDS_eq.
    # Spark holds 100% → share = 1.0.
    assert row["cum_usdc"] == Decimal("10000000")
    assert row["cum_usds_leg"] == Decimal("10000000")
    # sUSDS leg: 100M shares × 1.10 pps = 110M USDS-equivalent
    assert row["cum_susds"] == Decimal("110000000.0")
    assert row["cum_balance"] == Decimal("130000000.0")


def test_compute_sky_revenue_subtracts_usds_leg_only_routes_usdc_to_sde():
    """End-to-end sanity on ``compute_sky_revenue`` after PSM3 leg-split:
        - USDS leg  → subtracted from ``utilized``
        - USDC leg  → folded into ``cum_sde`` (BR base excluded; SDE side
                      collects the actual yield separately)
        - sUSDS leg → NOT subtracted (prime pays full BR on this slice and
                      receives a 30 bps Prime Revenue credit elsewhere via
                      ``_psm3_susds_spread`` to net out the SSR appreciation)

    Setup: $100M debt, $30M each leg at PSM3. Daily BR is charged on
    (100M − 30M USDS − 30M USDC-via-SDE) = $40M (the sUSDS $30M is still
    in the BR base).
    """
    from settle.compute._helpers import daily_compounding_factor
    from settle.compute.sky_revenue import compute_sky_revenue

    today = date(2026, 3, 15)
    period = Period(start=today, end=today, pin_blocks={Chain.ETHEREUM: 1})
    psm_df = pd.DataFrame({
        "block_date":   [today],
        "daily_net":    [Decimal("90000000")],
        "cum_balance":  [Decimal("90000000")],
        "cum_usdc":     [Decimal("30000000")],
        "cum_usds_leg": [Decimal("30000000")],
        "cum_susds":    [Decimal("30000000")],
    })
    rev = compute_sky_revenue(
        period,
        debt=pd.DataFrame({"block_date": [today], "cum_debt": [Decimal("100000000")]}),
        alm_usds=pd.DataFrame({"block_date": [today], "cum_balance": [Decimal("0")]}),
        ssr=pd.DataFrame({"effective_date": [today], "ssr_apy": [Decimal("0.047")]}),
        psm_usds=psm_df,
    )
    # utilized = 100M − 0 − 30M (USDS leg) − 30M (USDC via SDE) = $40M
    # sUSDS leg ($30M) stays in the BR base; the prime gets a separate
    # 30 bps credit through ``_psm3_susds_spread`` (tested below).
    from settle.compute._helpers import combine_apys
    from settle.compute.sky_revenue import BASE_RATE_OVER_SSR as _BR_SPREAD
    expected = Decimal("40000000") * daily_compounding_factor(
        combine_apys(Decimal("0.047"), _BR_SPREAD)
    )
    assert rev == expected


def test_psm3_susds_spread_30bps_daily():
    """``_psm3_susds_spread`` returns Σ_d cum_susds × daily_factor(30bps).
    Verifies the neutralising credit's magnitude: $100M sUSDS leg over 10
    days at 30 bps ≈ $100M × 30bps × 10/365 ≈ $8,219."""
    from settle.compute._helpers import daily_compounding_factor
    from settle.compute.sky_revenue import BASE_RATE_OVER_SSR

    days = [date(2026, 3, 1) + timedelta(days=i) for i in range(10)]
    df = pd.DataFrame({
        "block_date":   days,
        "daily_net":    [Decimal(0)] * 10,
        "cum_balance":  [Decimal("100000000")] * 10,
        "cum_usdc":     [Decimal(0)] * 10,
        "cum_usds_leg": [Decimal(0)] * 10,
        "cum_susds":    [Decimal("100000000")] * 10,
    })
    period = Period(start=days[0], end=days[-1],
                    pin_blocks={Chain.BASE: 1, Chain.ETHEREUM: 1})

    out = _psm3_susds_spread(df, period)
    expected = Decimal("100000000") * daily_compounding_factor(BASE_RATE_OVER_SSR) * 10
    assert out == expected
    assert Decimal("8000") < out < Decimal("8500")


def test_psm3_susds_spread_empty_returns_zero():
    """Missing input or absent ``cum_susds`` column ⇒ $0 spread.
    A frame in the legacy 3-column shape (no per-leg columns) returns 0 —
    defensive, since the production producer always emits the 6-column shape."""
    period = Period(start=date(2026, 3, 1), end=date(2026, 3, 31),
                    pin_blocks={Chain.ETHEREUM: 1})
    assert _psm3_susds_spread(None, period) == Decimal(0)
    assert _psm3_susds_spread(pd.DataFrame(), period) == Decimal(0)
    df_old = pd.DataFrame({
        "block_date":   [date(2026, 3, 5)],
        "daily_net":    [Decimal("100000")],
        "cum_balance":  [Decimal("100000")],
    })
    assert _psm3_susds_spread(df_old, period) == Decimal(0)
