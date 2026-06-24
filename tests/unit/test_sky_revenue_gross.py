"""Unit tests for ``compute_sky_revenue_daily``'s ``daily_sky_rev_gross``
column.

``daily_sky_rev_gross`` is the per-day BR × cum_debt with NO deductions
(no idle USDS, no SDE, no PSM, no Curve/lending idle). The orchestrator
sums it to write ``sky_revenue_gross`` into provenance for the monthly
report's "BR reduction from idle/SDE deductions" display.

Key properties under test:

* When utilized == cum_debt (no deductions), daily_sky_rev == daily_sky_rev_gross.
* When deductions reduce utilized below cum_debt, gross > actual on every
  affected day.
* When cum_debt == 0 on a day, both are 0 (no negative gross).
* Subsidy applies to both — the subsidy cap is on the principal in either
  branch, so gross and actual see the same subsidised slice / excess split
  (just with different principals).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from settle.compute._helpers import combine_apys, daily_compounding_factor
from settle.compute.sky_revenue import BASE_RATE_OVER_SSR, compute_sky_revenue_daily
from settle.domain import Chain, Period
from settle.domain.subsidy import ReferenceRateHistory, SubsidyConfig


def _period(start: date, end: date) -> Period:
    return Period(start=start, end=end, pin_blocks={Chain.ETHEREUM: 1})


def _empty(cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({c: [] for c in cols})


def _ssr_const(rate: float, since: date = date(2025, 1, 1)) -> pd.DataFrame:
    return pd.DataFrame({"effective_date": [since], "ssr_apy": [rate]})


def test_no_deductions_gross_equals_actual():
    """When utilized == cum_debt on every day (no idle / SDE / PSM /
    Curve / lending), daily_sky_rev_gross must equal daily_sky_rev on
    every day."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    debt = pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_debt": [100_000_000.0]})
    _total, daily, _ = compute_sky_revenue_daily(
        period,
        debt=debt,
        alm_usds=_empty(["block_date", "cum_balance"]),
        ssr=_ssr_const(0.047),
    )
    # 31 days, no deductions → gross == actual on every row
    assert len(daily) == 31
    assert (daily["daily_sky_rev"] == daily["daily_sky_rev_gross"]).all()
    # Both sums match the closed-form 31 × daily_factor × 100M
    expected_daily = Decimal("100000000") * daily_compounding_factor(
        combine_apys(Decimal("0.047"), BASE_RATE_OVER_SSR)
    )
    assert Decimal(str(daily["daily_sky_rev_gross"].sum())) == expected_daily * 31


def test_idle_alm_deduction_makes_gross_exceed_actual():
    """When ALM holds idle USDS, utilized < cum_debt → gross > actual
    on every day by exactly ``alm_usds × daily_factor``."""
    period = _period(date(2026, 3, 1), date(2026, 3, 1))   # single day
    debt = pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_debt": [100_000_000.0]})
    alm = pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_balance": [30_000_000.0]})
    _total, daily, _ = compute_sky_revenue_daily(
        period, debt=debt, alm_usds=alm, ssr=_ssr_const(0.047),
    )
    f = daily_compounding_factor(combine_apys(Decimal("0.047"), BASE_RATE_OVER_SSR))
    actual = Decimal("70000000") * f       # 100M - 30M = 70M utilized
    gross  = Decimal("100000000") * f
    assert Decimal(str(daily["daily_sky_rev"].iloc[0])) == actual
    assert Decimal(str(daily["daily_sky_rev_gross"].iloc[0])) == gross
    assert gross - actual == Decimal("30000000") * f   # idle slice's BR


def test_sde_deduction_makes_gross_exceed_actual():
    """When SDE asset value is deducted from utilized, gross > actual.
    The SDE revenue is booked separately by the orchestrator — the BR
    here only differs by the deducted slice."""
    period = _period(date(2026, 3, 1), date(2026, 3, 1))
    debt = pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_debt": [500_000_000.0]})
    sde  = pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_value":   [325_000_000.0]})
    _total, daily, _ = compute_sky_revenue_daily(
        period, debt=debt,
        alm_usds=_empty(["block_date", "cum_balance"]),
        ssr=_ssr_const(0.047),
        sde_asset_value=sde,
    )
    f = daily_compounding_factor(combine_apys(Decimal("0.047"), BASE_RATE_OVER_SSR))
    actual = Decimal("175000000") * f      # 500M - 325M utilized
    gross  = Decimal("500000000") * f
    assert Decimal(str(daily["daily_sky_rev"].iloc[0])) == actual
    assert Decimal(str(daily["daily_sky_rev_gross"].iloc[0])) == gross


def test_zero_debt_gives_zero_gross():
    """When cum_debt is 0 on a day (period before debt activity began),
    both daily_sky_rev and daily_sky_rev_gross are 0."""
    period = _period(date(2026, 3, 1), date(2026, 3, 1))
    # No row before period.start → cum_debt = 0
    debt = pd.DataFrame({"block_date": [date(2026, 6, 1)], "cum_debt": [100_000_000.0]})
    _total, daily, _ = compute_sky_revenue_daily(
        period, debt=debt,
        alm_usds=_empty(["block_date", "cum_balance"]),
        ssr=_ssr_const(0.047),
    )
    assert Decimal(str(daily["daily_sky_rev"].iloc[0])) == Decimal("0")
    assert Decimal(str(daily["daily_sky_rev_gross"].iloc[0])) == Decimal("0")


def test_gross_uses_subsidy_when_active():
    """Subsidy applies to both actual and gross — gross simply uses
    cum_debt as the principal. Cap-split logic is identical."""
    period = _period(date(2026, 3, 1), date(2026, 3, 1))
    # 200M debt, no deductions → utilized = 200M = cum_debt → gross == actual
    # but both go through the subsidy branch (sub_p = min(200M, 100M cap) = 100M,
    # excess = 100M at full BR).
    debt = pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_debt": [200_000_000.0]})
    subsidy = SubsidyConfig(
        enabled=True,
        program_start=date(2026, 1, 1),
        cap_usd=Decimal("100000000"),
        ramp_months=24,
        ref_rate_kind="tbill_3m",
    )
    ref_rates = ReferenceRateHistory(
        rates=pd.DataFrame({
            "effective_date": [date(2026, 1, 1), date(2026, 2, 15), date(2026, 3, 1)],
            "ref_rate_apy":   [Decimal("0.0367"), Decimal("0.0367"), Decimal("0.0367")],
        }),
        kind="tbill_3m",
    )
    _total, daily, _ = compute_sky_revenue_daily(
        period, debt=debt,
        alm_usds=_empty(["block_date", "cum_balance"]),
        ssr=_ssr_const(0.047),
        subsidy_config=subsidy, ref_rate_history=ref_rates,
    )
    # No deductions → identical principals → identical revenue
    assert daily["daily_sky_rev"].iloc[0] == daily["daily_sky_rev_gross"].iloc[0]


def test_gross_sums_match_orchestrator_pattern():
    """The orchestrator computes ``sky_revenue_gross = Σ daily_sky_rev_gross``.
    Verify the sum is well-defined and equals the closed form over
    constant cum_debt + constant SSR."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    debt = pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_debt": [250_000_000.0]})
    # Add deductions so actual differs from gross — the test is the gross sum
    alm = pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_balance": [10_000_000.0]})
    _total, daily, _ = compute_sky_revenue_daily(
        period, debt=debt, alm_usds=alm, ssr=_ssr_const(0.047),
    )
    f = daily_compounding_factor(combine_apys(Decimal("0.047"), BASE_RATE_OVER_SSR))
    expected_gross_total = Decimal("250000000") * 31 * f
    expected_actual_total = Decimal("240000000") * 31 * f
    gross_sum = Decimal(str(daily["daily_sky_rev_gross"].sum()))
    actual_sum = Decimal(str(daily["daily_sky_rev"].sum()))
    assert gross_sum == expected_gross_total
    assert actual_sum == expected_actual_total
    assert gross_sum > actual_sum
