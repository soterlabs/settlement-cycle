"""Unit tests for `settle.compute.agent_rate`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from settle.compute._helpers import daily_compounding_factor
from settle.compute.agent_rate import (
    AGENT_RATE_OVER_SSR,
    AGENT_RATE_SUSDS_ONLY,
    compute_agent_rate,
)
from settle.domain import Chain, Period


def _period(start: date, end: date) -> Period:
    return Period(start=start, end=end, pin_blocks={Chain.ETHEREUM: 1})


def _empty(cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({c: [] for c in cols})


def _ssr_const(rate: float, since: date = date(2025, 1, 1)) -> pd.DataFrame:
    return pd.DataFrame({"effective_date": [since], "ssr_apy": [rate]})


def test_zero_subproxy_balances_zero_rate():
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    rate = compute_agent_rate(
        period,
        subproxy_usds=_empty(["block_date", "cum_balance"]),
        subproxy_susds=_empty(["block_date", "cum_balance"]),
        ssr=_ssr_const(0.04),
    )
    assert rate == Decimal("0")


def test_constants_match_methodology():
    """Agent rate = SSR + 20bps. For sUSDS, SSR is in the index → only the
    +20bps applies."""
    assert AGENT_RATE_OVER_SSR == Decimal("0.002")
    assert AGENT_RATE_SUSDS_ONLY == Decimal("0.002")


def test_usds_only_for_31_days():
    """20M USDS in subproxy × 31 days at SSR=4.0% → APY = 4.0+0.2 = 4.2%."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    rate = compute_agent_rate(
        period,
        subproxy_usds=pd.DataFrame({"block_date": [date(2025, 12, 1)], "cum_balance": [20_000_000.0]}),
        subproxy_susds=_empty(["block_date", "cum_balance"]),
        ssr=_ssr_const(0.04),
    )
    expected = Decimal("20000000") * 31 * daily_compounding_factor(Decimal("0.042"))
    assert rate == expected


def test_susds_only_uses_flat_two_pct():
    """5M sUSDS earns flat 0.20% APY independent of SSR."""
    period = _period(date(2026, 3, 1), date(2026, 3, 1))    # 1 day
    rate = compute_agent_rate(
        period,
        subproxy_usds=_empty(["block_date", "cum_balance"]),
        subproxy_susds=pd.DataFrame({"block_date": [date(2025, 12, 1)], "cum_balance": [5_000_000.0]}),
        ssr=_ssr_const(0.99),                                # SSR is irrelevant for sUSDS
    )
    expected = Decimal("5000000") * daily_compounding_factor(AGENT_RATE_SUSDS_ONLY)
    assert rate == expected


def test_combined_usds_and_susds():
    """Both balances contribute — sum is additive."""
    period = _period(date(2026, 3, 1), date(2026, 3, 1))
    rate = compute_agent_rate(
        period,
        subproxy_usds=pd.DataFrame({"block_date": [date(2026, 2, 1)], "cum_balance": [20_000_000.0]}),
        subproxy_susds=pd.DataFrame({"block_date": [date(2026, 2, 1)], "cum_balance": [5_000_000.0]}),
        ssr=_ssr_const(0.04),
    )
    expected_usds = Decimal("20000000") * daily_compounding_factor(Decimal("0.042"))
    expected_susds = Decimal("5000000") * daily_compounding_factor(Decimal("0.002"))
    assert rate == expected_usds + expected_susds


def test_handles_balance_change_mid_period():
    """OBEX scenario: 21M USDS at start, +442,327 on 2026-02-02 (MSC #4 settlement)."""
    period = _period(date(2026, 2, 1), date(2026, 2, 5))    # 5 days
    bal = pd.DataFrame({
        "block_date": [date(2025, 11, 17), date(2026, 2, 2)],
        "cum_balance": [21_000_000.0, 21_442_327.0],
    })
    rate = compute_agent_rate(
        period,
        subproxy_usds=bal,
        subproxy_susds=_empty(["block_date", "cum_balance"]),
        ssr=_ssr_const(0.04),
    )
    f = daily_compounding_factor(Decimal("0.042"))
    # Feb 1: 21,000,000 (still pre-settlement)
    # Feb 2-5: 21,442,327 (post-settlement)
    expected = Decimal("21000000") * f + Decimal("21442327") * 4 * f
    assert rate == expected
