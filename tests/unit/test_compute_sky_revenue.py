"""Unit tests for `settle.compute.sky_revenue`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from settle.compute._helpers import combine_apys, daily_compounding_factor
from settle.compute.sky_revenue import (
    BASE_RATE_OVER_SSR,
    compute_sky_revenue,
    compute_sky_revenue_daily,
    summarize_subsidy,
)
from settle.domain import Chain, Period
from settle.domain.subsidy import ReferenceRateHistory, SubsidyConfig


def _period(start: date, end: date) -> Period:
    return Period(start=start, end=end, pin_blocks={Chain.ETHEREUM: 1})


def _empty(cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({c: [] for c in cols})


def _ssr_const(rate: float, since: date = date(2025, 1, 1)) -> pd.DataFrame:
    return pd.DataFrame({
        "effective_date": [since],
        "ssr_apy":        [rate],
    })


def test_zero_debt_zero_revenue():
    """A non-empty debt timeseries pinned to ``cum_debt=0`` expresses
    "no debt activity" → zero revenue. Distinct from a *missing* debt
    timeseries, which now raises (see ``test_empty_debt_raises``)."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    zero_debt = pd.DataFrame({
        "block_date": [date(2025, 11, 17)], "cum_debt": [0.0],
    })
    rev = compute_sky_revenue(
        period,
        debt=zero_debt,
        alm_usds=_empty(["block_date", "cum_balance"]),
        ssr=_ssr_const(0.04),
    )
    assert rev == Decimal("0")


def test_empty_debt_raises():
    """An empty debt timeseries almost certainly signals a misconfigured Dune
    source (wrong ``ilk_bytes32``, query failure). The compute layer must fail
    loud rather than silently produce ``$0`` of sky revenue."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    with pytest.raises(ValueError, match="debt timeseries is empty"):
        compute_sky_revenue(
            period,
            debt=_empty(["block_date", "cum_debt"]),
            alm_usds=_empty(["block_date", "cum_balance"]),
            ssr=_ssr_const(0.04),
        )


def test_constant_debt_constant_ssr_31_days():
    """100M utilized × 31 days at SSR=4.7% (borrow=5.0%). Sum should equal 31×daily_factor×100M."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))     # 31 days
    debt_df = pd.DataFrame({"block_date": [date(2025, 11, 17)], "cum_debt": [100_000_000.0]})

    rev = compute_sky_revenue(
        period,
        debt=debt_df,
        alm_usds=_empty(["block_date", "cum_balance"]),
        ssr=_ssr_const(0.047),                                 # borrow = 5.0%
    )
    # The charge COMPOUNDS (2026-08-24): for a constant principal and rate
    # the closed form is P × ((1+f)^n − 1) — analytically Σ over n days of
    # (P + accrued) × f, and equal to P × ((1+APY)^(n/365) − 1).
    f = daily_compounding_factor(
        combine_apys(Decimal("0.047"), BASE_RATE_OVER_SSR)
    )
    expected = Decimal("100000000") * ((1 + f) ** 31 - 1)
    # Tolerance: the closed form and the day-by-day accumulation differ only
    # in Decimal context rounding (~1e-22 on $400K).
    assert abs(rev - expected) < Decimal("1e-9")
    # Compounding adds ~0.2% over the simple sum of daily interest.
    assert rev > Decimal("100000000") * f * 31
    # ~$100M × 31 × 0.0001337 ≈ $414K — sanity bound
    assert Decimal("400000") < rev < Decimal("420000")


def test_subtracts_alm_balance_from_utilized():
    """Daily revenue uses utilized = debt − alm_usds.
    Subproxy USDS/sUSDS are treasury/risk capital and are NOT deducted from utilized."""
    period = _period(date(2026, 3, 1), date(2026, 3, 1))      # 1 day
    rev = compute_sky_revenue(
        period,
        debt=pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_debt": [100_000_000.0]}),
        alm_usds=pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_balance": [3_000_000.0]}),
        ssr=_ssr_const(0.047),
    )
    utilized = Decimal("100000000") - Decimal("3000000")
    expected = utilized * daily_compounding_factor(
        combine_apys(Decimal("0.047"), BASE_RATE_OVER_SSR)
    )
    assert rev == expected


def test_handles_ssr_change_mid_period():
    """SSR drops 4.0% → 3.75% on March 9. First 8 days at 4.0%, remaining 23 at 3.75%."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    debt_df = pd.DataFrame({"block_date": [date(2025, 12, 1)], "cum_debt": [100_000_000.0]})
    ssr_df = pd.DataFrame({
        "effective_date": [date(2025, 12, 16), date(2026, 3, 9)],
        "ssr_apy":        [0.0400,             0.0375],
    })

    rev = compute_sky_revenue(
        period,
        debt=debt_df,
        alm_usds=_empty(["block_date", "cum_balance"]),
        ssr=ssr_df,
    )

    f1 = daily_compounding_factor(combine_apys(Decimal("0.0400"), BASE_RATE_OVER_SSR))
    f2 = daily_compounding_factor(combine_apys(Decimal("0.0375"), BASE_RATE_OVER_SSR))
    # March 1-8 at 4.00% + spread = 4.30% → 8 days
    # March 9-31 at 3.75% + spread = 4.05% → 23 days
    # Compounding across the rate step: the 8 days at f1 accrue, then that
    # accrued balance earns f2 for the remaining 23 days.
    P = Decimal("100000000")
    stage1 = P * ((1 + f1) ** 8 - 1)
    expected = (P + stage1) * ((1 + f2) ** 23 - 1) + stage1
    assert abs(rev - expected) < Decimal("1e-9")


def test_skips_days_when_utilized_is_negative():
    """Utilized can be slightly negative if ALM holds more than debt
    (briefly during deposit-then-redeem). Treat these days as zero contribution."""
    period = _period(date(2026, 3, 1), date(2026, 3, 1))
    rev = compute_sky_revenue(
        period,
        debt=pd.DataFrame({"block_date": [date(2025, 12, 1)], "cum_debt": [100_000_000.0]}),
        alm_usds=pd.DataFrame({"block_date": [date(2025, 12, 1)], "cum_balance": [200_000_000.0]}),
        ssr=_ssr_const(0.04),
    )
    assert rev == Decimal("0")


def test_borrow_rate_spread_schedule():
    """RULES.md Rule 4 spread, dated: 30bps at inception, 20bps from the
    2026-07-23 Stability Scope change (same vote that cut SSR to 3.52%)."""
    from settle.compute.sky_revenue import base_rate_spread_at

    assert BASE_RATE_OVER_SSR == Decimal("0.003")   # pre-cutover constant
    assert base_rate_spread_at(date(2024, 1, 1)) == Decimal("0.003")
    assert base_rate_spread_at(date(2026, 7, 22)) == Decimal("0.003")
    # The whole cutover day uses the new spread (end-of-day carry-forward,
    # matching the SSR series convention).
    assert base_rate_spread_at(date(2026, 7, 23)) == Decimal("0.002")
    assert base_rate_spread_at(date(2027, 1, 1)) == Decimal("0.002")


def test_subsidy_enabled_but_period_before_program_start():
    """Periods before program_start (2026-01-01) must not call ref_rate_history.at
    and must fall back to full BR — the subsidy hadn't started yet.

    Regression for: ValueError: No reference rate found ≤ 2025-12-01.
    The ref_rate_history here intentionally has no entries before 2026-01-01
    to confirm the guard prevents the lookup.
    """
    period = _period(date(2025, 12, 1), date(2025, 12, 31))
    debt_df = pd.DataFrame({"block_date": [date(2025, 11, 1)], "cum_debt": [100_000_000.0]})
    ssr_df  = _ssr_const(0.04)

    subsidy = SubsidyConfig(
        enabled=True,
        program_start=date(2026, 1, 1),
        cap_usd=Decimal("1000000000"),
        ramp_months=24,
        ref_rate_kind="tbill_3m",
    )
    # Intentionally empty before 2026-01-01 — the fix must not reach .at()
    ref_rates = ReferenceRateHistory(
        rates=pd.DataFrame({
            "effective_date": [date(2026, 1, 1)],
            "ref_rate_apy":   [Decimal("0.0367")],
        }),
        kind="tbill_3m",
    )

    rev = compute_sky_revenue(
        period,
        debt=debt_df,
        alm_usds=_empty(["block_date", "cum_balance"]),
        ssr=ssr_df,
        subsidy_config=subsidy,
        ref_rate_history=ref_rates,
    )

    # Expect full BR (no subsidy) for all 31 days of Dec 2025.
    base_apy = combine_apys(Decimal("0.04"), BASE_RATE_OVER_SSR)
    f = daily_compounding_factor(base_apy)
    expected = Decimal("100000000") * ((1 + f) ** 31 - 1)
    assert abs(rev - expected) < Decimal("1e-9")


def test_subsidy_zero_benefit_warns(caplog):
    """When the reference rate sits at/above base every day, the ramp clamps
    to base and the subsidy yields $0 — the stale/placeholder-rate signature.
    Compute must warn (the May 2026 Spark mis-pricing went unflagged because
    a Jan reference rate of 4.33% > BR silently nullified the subsidy)."""
    import logging

    period = _period(date(2026, 5, 1), date(2026, 5, 31))
    debt_df = pd.DataFrame({"block_date": [date(2026, 4, 1)], "cum_debt": [3_000_000_000.0]})
    ssr_df = _ssr_const(0.0365)  # base ≈ 3.95% < stale ref 4.33%
    subsidy = SubsidyConfig(
        enabled=True, program_start=date(2026, 1, 1),
        cap_usd=Decimal("1000000000"), ramp_months=24, ref_rate_kind="tbill_3m",
    )
    ref_rates = ReferenceRateHistory(
        rates=pd.DataFrame({
            "effective_date": [date(2026, 5, 1)],
            "ref_rate_apy":   [Decimal("0.0433")],   # placeholder value above BR
        }),
        kind="tbill_3m",
    )
    with caplog.at_level(logging.WARNING):
        _total, df, _ = compute_sky_revenue_daily(
            period, debt_df, _empty(["block_date", "cum_balance"]), ssr_df,
            subsidy_config=subsidy, ref_rate_history=ref_rates,
        )
    assert any("$0 benefit" in r.message for r in caplog.records)
    # Every day's subsidised rate clamped up to base (no benefit).
    assert all(r["sub_apy"] == r["base_apy"] for r in df.to_dict("records"))


def test_subsidy_real_benefit_does_not_warn(caplog):
    """Sanity counterpart: when ref_rate < base, the subsidy applies and no
    zero-benefit warning fires."""
    import logging

    period = _period(date(2026, 5, 1), date(2026, 5, 31))
    debt_df = pd.DataFrame({"block_date": [date(2026, 4, 1)], "cum_debt": [3_000_000_000.0]})
    ssr_df = _ssr_const(0.0365)
    subsidy = SubsidyConfig(
        enabled=True, program_start=date(2026, 1, 1),
        cap_usd=Decimal("1000000000"), ramp_months=24, ref_rate_kind="tbill_3m",
    )
    ref_rates = ReferenceRateHistory(
        rates=pd.DataFrame({
            "effective_date": [date(2026, 5, 1)],
            "ref_rate_apy":   [Decimal("0.0362")],   # real T-Bill, below BR
        }),
        kind="tbill_3m",
    )
    with caplog.at_level(logging.WARNING):
        compute_sky_revenue_daily(
            period, debt_df, _empty(["block_date", "cum_balance"]), ssr_df,
            subsidy_config=subsidy, ref_rate_history=ref_rates,
        )
    assert not any("$0 benefit" in r.message for r in caplog.records)


def test_summarize_subsidy_reconciles():
    """summarize_subsidy is the single source for the report: its tranche
    CoFs sum to actual_cof, actual_cof equals the summed daily charge, and
    full_br − actual == subsidy_benefit (all on the same compounding factor)."""
    from settle.compute.sky_revenue import summarize_subsidy
    period = _period(date(2026, 5, 1), date(2026, 5, 31))
    debt_df = pd.DataFrame({"block_date": [date(2026, 4, 1)], "cum_debt": [3_000_000_000.0]})
    ssr_df = _ssr_const(0.0365)
    subsidy = SubsidyConfig(
        enabled=True, program_start=date(2026, 1, 1),
        cap_usd=Decimal("1000000000"), ramp_months=24, ref_rate_kind="tbill_3m",
    )
    ref = ReferenceRateHistory(
        rates=pd.DataFrame({"effective_date": [date(2026, 5, 1)],
                            "ref_rate_apy": [Decimal("0.0362")]}), kind="tbill_3m")
    total, df, _ = compute_sky_revenue_daily(
        period, debt_df, _empty(["block_date", "cum_balance"]), ssr_df,
        subsidy_config=subsidy, ref_rate_history=ref)
    s = summarize_subsidy(df, subsidy)
    assert s is not None
    sub_cof, exc_cof = Decimal(s["sub_tranche_cof"]), Decimal(s["exc_tranche_cof"])
    actual, full_br = Decimal(s["actual_cof"]), Decimal(s["full_br_cof"])
    benefit = Decimal(s["subsidy_benefit"])
    assert abs(sub_cof + exc_cof - actual) < Decimal("0.01")        # tranches tie to actual
    assert abs(actual - total) < Decimal("0.01")                     # actual == summed daily charge
    assert abs(full_br - actual - benefit) < Decimal("0.0000001")    # benefit identity
    assert s["zero_benefit"] is False and benefit > 0
    assert s["ref_rate_kind"] == "tbill_3m"


def test_summarize_subsidy_none_when_disabled():
    from settle.compute.sky_revenue import summarize_subsidy
    df = pd.DataFrame({"utilized": [1.0], "base_apy": [0.04],
                       "sub_apy": [None], "ref_rate_apy": [None]})
    assert summarize_subsidy(df, None) is None
    assert summarize_subsidy(df, SubsidyConfig(enabled=False)) is None


def test_summarize_subsidy_handles_program_start_midperiod(caplog):
    """A period that straddles program_start has mixed None/float sub_apy →
    pandas coerces the column to float64 with NaN. summarize_subsidy must not
    crash on Decimal('NaN') < base, and must still reconcile. Regression for
    the InvalidOperation introduced by the panel→provenance refactor."""
    import logging
    period = _period(date(2026, 2, 1), date(2026, 2, 3))   # program starts mid-window
    debt_df = pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_debt": [3_000_000_000.0]})
    ssr_df = _ssr_const(0.0365)
    subsidy = SubsidyConfig(
        enabled=True, program_start=date(2026, 2, 2),
        cap_usd=Decimal("1000000000"), ramp_months=24, ref_rate_kind="tbill_3m")
    ref = ReferenceRateHistory(
        rates=pd.DataFrame({"effective_date": [date(2026, 2, 2)],
                            "ref_rate_apy": [Decimal("0.0362")]}), kind="tbill_3m")
    total, df, _ = compute_sky_revenue_daily(
        period, debt_df, _empty(["block_date", "cum_balance"]), ssr_df,
        subsidy_config=subsidy, ref_rate_history=ref)
    s = summarize_subsidy(df, subsidy)             # must not raise
    assert s is not None
    # tranche CoFs + actual still reconcile across the mixed window
    assert abs(Decimal(s["sub_tranche_cof"]) + Decimal(s["exc_tranche_cof"])
               - Decimal(s["actual_cof"])) < Decimal("0.01")
    assert abs(Decimal(s["actual_cof"]) - total) < Decimal("0.01")


def test_summarize_subsidy_no_warning_before_program_start(caplog):
    """A whole period before program_start (active==0) is 'subsidy not started',
    not '$0 benefit from a stale rate' — no zero-benefit flag, no warning."""
    import logging
    period = _period(date(2025, 12, 1), date(2025, 12, 31))
    debt_df = pd.DataFrame({"block_date": [date(2025, 11, 1)], "cum_debt": [3_000_000_000.0]})
    ssr_df = _ssr_const(0.04)
    subsidy = SubsidyConfig(
        enabled=True, program_start=date(2026, 1, 1),
        cap_usd=Decimal("1000000000"), ramp_months=24, ref_rate_kind="tbill_3m")
    ref = ReferenceRateHistory(
        rates=pd.DataFrame({"effective_date": [date(2026, 1, 1)],
                            "ref_rate_apy": [Decimal("0.0362")]}), kind="tbill_3m")
    with caplog.at_level(logging.WARNING):
        _t, df, _ = compute_sky_revenue_daily(
            period, debt_df, _empty(["block_date", "cum_balance"]), ssr_df,
            subsidy_config=subsidy, ref_rate_history=ref)
    s = summarize_subsidy(df, subsidy)
    assert s["zero_benefit"] is False                      # not flagged
    assert not any("$0 benefit" in r.message for r in caplog.records)
