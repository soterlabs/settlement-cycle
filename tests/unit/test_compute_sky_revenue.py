"""Unit tests for `settle.compute.sky_revenue`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from settle.compute._helpers import daily_compounding_factor
from settle.compute.sky_revenue import BASE_RATE_OVER_SSR, compute_sky_revenue
from settle.domain import Chain, Period


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
        subproxy_usds=_empty(["block_date", "cum_balance"]),
        subproxy_susds_principal=_empty(["block_date", "cum_balance"]),
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
            subproxy_usds=_empty(["block_date", "cum_balance"]),
            subproxy_susds_principal=_empty(["block_date", "cum_balance"]),
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
        subproxy_usds=_empty(["block_date", "cum_balance"]),
        subproxy_susds_principal=_empty(["block_date", "cum_balance"]),
        alm_usds=_empty(["block_date", "cum_balance"]),
        ssr=_ssr_const(0.047),                                 # borrow = 5.0%
    )
    expected_daily = Decimal("100000000") * daily_compounding_factor(Decimal("0.05"))
    expected = expected_daily * 31
    assert rev == expected
    # ~$100M × 31 × 0.0001337 ≈ $414K — sanity bound
    assert Decimal("400000") < rev < Decimal("420000")


def test_subtracts_subproxy_balances_from_utilized():
    """Daily revenue uses utilized = debt − subproxy_usds − subproxy_susds − alm_usds."""
    period = _period(date(2026, 3, 1), date(2026, 3, 1))      # 1 day
    rev = compute_sky_revenue(
        period,
        debt=pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_debt": [100_000_000.0]}),
        subproxy_usds=pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_balance": [10_000_000.0]}),
        subproxy_susds_principal=pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_balance": [5_000_000.0]}),
        alm_usds=pd.DataFrame({"block_date": [date(2026, 1, 1)], "cum_balance": [3_000_000.0]}),
        ssr=_ssr_const(0.047),
    )
    utilized = Decimal("100000000") - Decimal("10000000") - Decimal("5000000") - Decimal("3000000")
    expected = utilized * daily_compounding_factor(Decimal("0.05"))
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
        subproxy_usds=_empty(["block_date", "cum_balance"]),
        subproxy_susds_principal=_empty(["block_date", "cum_balance"]),
        alm_usds=_empty(["block_date", "cum_balance"]),
        ssr=ssr_df,
    )

    f1 = daily_compounding_factor(Decimal("0.0400") + BASE_RATE_OVER_SSR)
    f2 = daily_compounding_factor(Decimal("0.0375") + BASE_RATE_OVER_SSR)
    # March 1-8 at 4.00% + spread = 4.30% → 8 days
    # March 9-31 at 3.75% + spread = 4.05% → 23 days
    expected = Decimal("100000000") * (8 * f1 + 23 * f2)
    assert rev == expected


def test_skips_days_when_utilized_is_negative():
    """Utilized can be slightly negative if subproxy/ALM hold more than debt
    (briefly during deposit-then-redeem). Treat these days as zero contribution."""
    period = _period(date(2026, 3, 1), date(2026, 3, 1))
    rev = compute_sky_revenue(
        period,
        debt=pd.DataFrame({"block_date": [date(2025, 12, 1)], "cum_debt": [100_000_000.0]}),
        subproxy_usds=pd.DataFrame({"block_date": [date(2025, 12, 1)], "cum_balance": [200_000_000.0]}),
        subproxy_susds_principal=_empty(["block_date", "cum_balance"]),
        alm_usds=_empty(["block_date", "cum_balance"]),
        ssr=_ssr_const(0.04),
    )
    assert rev == Decimal("0")


def test_borrow_rate_spread_is_30_bps():
    """Constant from RULES.md Rule 4: borrow rate = SSR + 0.30%."""
    assert BASE_RATE_OVER_SSR == Decimal("0.003")
