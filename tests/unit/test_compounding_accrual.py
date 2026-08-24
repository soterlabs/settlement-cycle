"""Unit tests for ``CompoundingAccrual`` — the BR/SSR compounding convention.

Operator decision 2026-08-24: the Base Rate and SSR-derived accruals
compound (interest earns interest), instead of summing simple daily
interest. The closed forms asserted here are derived analytically, not
copied from the implementation:

    constant principal P, constant daily factor f, n days
      → total = P × ((1+f)^n − 1)
      → and since f = (1+APY)^(1/365) − 1, that is P × ((1+APY)^(n/365) − 1)
        i.e. exactly n days of the on-chain per-second accrual.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

import pytest

from settle.compute._helpers import (
    CompoundingAccrual,
    daily_compounding_factor,
)

_TOL = Decimal("1e-9")


@pytest.fixture(autouse=True)
def _high_precision():
    """These tests compare 31-/365-term products against closed forms, which
    needs more than the default 28 significant digits. Scoped via
    ``localcontext`` — a module-level ``getcontext().prec = 40`` would leak
    into every other test module (pytest imports all of them at collection),
    silently validating the suite's exact-Decimal reconciliation assertions
    at a precision production never runs at."""
    with localcontext() as ctx:
        ctx.prec = 40
        yield


def test_constant_principal_matches_closed_form():
    P, apy, n = Decimal("1000000000"), Decimal("0.0372"), 31
    f = daily_compounding_factor(apy)
    acc = CompoundingAccrual()
    for _ in range(n):
        acc.add(P, f)
    assert abs(acc.total - P * ((1 + f) ** n - 1)) < _TOL


def test_equals_per_second_compounding_over_the_period():
    """The whole point: a month of daily compounding reproduces the APY's
    per-second accrual over the same span."""
    P, apy, n = Decimal("1400000000"), Decimal("0.0352"), 31
    f = daily_compounding_factor(apy)
    acc = CompoundingAccrual()
    for _ in range(n):
        acc.add(P, f)
    per_second = P * ((1 + apy) ** (Decimal(n) / 365) - 1)
    # Float rounding inside daily_compounding_factor is the only difference.
    assert abs(acc.total - per_second) < Decimal("0.01")


def test_exceeds_simple_sum_by_expected_margin():
    """~0.15% of the interest over a 31-day month — the size of the bias the
    change removes."""
    P, apy, n = Decimal("1400000000"), Decimal("0.0372"), 31
    f = daily_compounding_factor(apy)
    acc = CompoundingAccrual()
    for _ in range(n):
        acc.add(P, f)
    simple = P * f * n
    assert acc.total > simple
    ratio = acc.total / simple - 1
    assert Decimal("0.0014") < ratio < Decimal("0.0016")


def test_uninterrupted_year_reproduces_the_quoted_apy():
    """An accrual left to run for 365 days without interruption reproduces
    the quoted APY, whereas summing the same 365 daily factors yields
    ``ln(1+APY)`` (3.6527% at APY 3.72%) — 6.7 bps less.

    This is a property of the accumulator, NOT a claim about a settlement
    year: in production each month's charge is capitalised into the ilk
    debt at settlement (``vat.grab`` positive dart), so the cross-month
    compounding happens through the growing principal rather than through
    an uninterrupted accrued balance."""
    P, apy = Decimal("1000000000"), Decimal("0.0372")
    f = daily_compounding_factor(apy)
    acc = CompoundingAccrual()
    for _ in range(365):
        acc.add(P, f)
    assert abs(acc.total / P - apy) < Decimal("0.0000001")
    assert apy - f * 365 > Decimal("0.00065")   # ≈ 6.7 bps


def test_rate_change_mid_period_applies_to_accrued_balance():
    P = Decimal("100000000")
    f1 = daily_compounding_factor(Decimal("0.0400"))
    f2 = daily_compounding_factor(Decimal("0.0375"))
    acc = CompoundingAccrual()
    for _ in range(8):
        acc.add(P, f1)
    stage1 = acc.total
    for _ in range(23):
        acc.add(P, f2)
    expected = (P + stage1) * ((1 + f2) ** 23 - 1) + stage1
    assert abs(acc.total - expected) < _TOL


def test_varying_principal_charges_each_day_on_that_days_balance():
    f = daily_compounding_factor(Decimal("0.05"))
    balances = [Decimal("1000000"), Decimal("3000000"), Decimal("2000000")]
    acc = CompoundingAccrual()
    increments = [acc.add(b, f) for b in balances]
    # Each increment is (balance + accrued-so-far) × f, and the increments
    # sum to the total (so per-day report rows still reconcile).
    assert abs(sum(increments, Decimal("0")) - acc.total) < _TOL
    manual = Decimal("0")
    for b in balances:
        manual += (b + manual) * f
    assert abs(acc.total - manual) < _TOL


def test_add_on_a_zero_principal_day_still_charges_accrued():
    """``add`` itself charges the accrued balance even when principal is 0.

    Callers must decide whether that is wanted: every production caller
    SKIPS days with a non-positive value (``_accrue_daily``, the sky_revenue
    charge path, the Curve venue loop), because the accrued interest is
    capitalised into the ilk at settlement rather than carried as an
    interest-bearing balance here — and because the actual charge and the
    ``full_br`` counterfactual have to accrue on identical footing or
    ``subsidy_benefit`` reports money Sky never gave up."""
    f = daily_compounding_factor(Decimal("0.05"))
    acc = CompoundingAccrual()
    acc.add(Decimal("1000000"), f)
    after_first = acc.total
    acc.add(Decimal("0"), f)
    assert abs(acc.total - after_first * (1 + f)) < _TOL


def test_per_venue_accrual_diverges_when_active_windows_differ():
    """Two $500M positions on disjoint halves of a 31-day month: a venue
    with no position skips accrual, so its accrued balance stops earning,
    while a pooled accumulator keeps compounding. Pins the ~$3.60 gap the
    per-venue comment documents."""
    f = daily_compounding_factor(Decimal("0.002"))
    P = Decimal("500000000")
    a, b, agg = CompoundingAccrual(), CompoundingAccrual(), CompoundingAccrual()
    for day in range(31):
        if day < 15:
            a.add(P, f)
        else:
            b.add(P, f)
        agg.add(P, f)          # pooled: always $500M held by someone
    split = a.total + b.total
    assert split < agg.total
    assert Decimal("3") < agg.total - split < Decimal("4")


def test_per_venue_accrual_equals_aggregate_accrual_when_windows_align():
    """With a common daily factor AND every venue active on the same days,
    ``(ΣP_v + Σacc_v) × f == Σ (P_v + acc_v) × f``, so splitting the accrual
    by venue matches accruing the aggregate. See the divergent case below —
    the equivalence is conditional, not general."""
    f = daily_compounding_factor(Decimal("0.003"))
    per_venue = [Decimal("400000000"), Decimal("250000000"), Decimal("90000000")]
    split = [CompoundingAccrual() for _ in per_venue]
    agg = CompoundingAccrual()
    for _ in range(31):
        for acc, P in zip(split, per_venue, strict=True):
            acc.add(P, f)
        agg.add(sum(per_venue, Decimal("0")), f)
    assert abs(sum((a.total for a in split), Decimal("0")) - agg.total) < _TOL
