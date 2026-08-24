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

from decimal import Decimal, getcontext

from settle.compute._helpers import (
    CompoundingAccrual,
    daily_compounding_factor,
)

getcontext().prec = 40

_TOL = Decimal("1e-9")


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


def test_a_full_year_bills_the_apy_not_its_log():
    """Simple summing billed ``ln(1+APY)`` (3.6527% at APY 3.72%) — a 6.7bps
    shortfall. Compounding recovers the quoted APY."""
    P, apy = Decimal("1000000000"), Decimal("0.0372")
    f = daily_compounding_factor(apy)
    acc = CompoundingAccrual()
    for _ in range(365):
        acc.add(P, f)
    assert abs(acc.total / P - apy) < Decimal("0.0000001")
    simple_rate = f * 365
    assert apy - simple_rate > Decimal("0.00065")   # ≈ 6.7 bps


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


def test_zero_principal_days_do_not_stop_accrued_from_earning():
    f = daily_compounding_factor(Decimal("0.05"))
    acc = CompoundingAccrual()
    acc.add(Decimal("1000000"), f)
    after_first = acc.total
    acc.add(Decimal("0"), f)          # position closed, accrued still earns
    assert acc.total > after_first
    assert abs(acc.total - after_first * (1 + f)) < _TOL


def test_add_interest_equivalent_to_add():
    """``add_interest`` is the pre-multiplied variant used where per-venue
    amounts are summed before accrual."""
    f = daily_compounding_factor(Decimal("0.03"))
    P = Decimal("5000000")
    a, b = CompoundingAccrual(), CompoundingAccrual()
    for _ in range(10):
        a.add(P, f)
        b.add_interest(P * f, f)
    assert abs(a.total - b.total) < _TOL
