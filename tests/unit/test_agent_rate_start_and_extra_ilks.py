"""agent_rate_start_date gating + multi-ilk debt summation (Diamond PAU)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from settle.compute.agent_rate import compute_agent_rate
from settle.domain.period import Period


def _period_july():
    return Period(start=date(2026, 7, 1), end=date(2026, 7, 31), pin_blocks={})


def _balances(amount: str, since: str):
    return pd.DataFrame({
        "block_date": [date.fromisoformat(since)],
        "daily_net": [Decimal(amount)],
        "cum_balance": [Decimal(amount)],
    })


def _flat_ssr(apy: str = "0.0352"):
    return pd.DataFrame({
        "effective_date": [date(2024, 9, 1)],
        "ssr_apy": [Decimal(apy)],
    })


def test_agent_rate_start_date_gates_accrual():
    usds = _balances("10000000", "2026-03-30")   # Osero: seeded pre-allocation
    susds = pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})
    full = compute_agent_rate(_period_july(), usds, susds, _flat_ssr())
    gated = compute_agent_rate(
        _period_july(), usds, susds, _flat_ssr(), start_date=date(2026, 7, 20),
    )
    # 12 accruing days (Jul 20–31) out of 31.
    assert gated == full / 31 * 12
    # No gate (default) == full month.
    assert compute_agent_rate(_period_july(), usds, susds, _flat_ssr(),
                              start_date=None) == full


def test_agent_rate_start_after_period_is_zero():
    usds = _balances("10000000", "2026-03-30")
    susds = pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})
    assert compute_agent_rate(
        _period_july(), usds, susds, _flat_ssr(), start_date=date(2026, 8, 1),
    ) == Decimal("0")


def test_mock_debt_source_dispatches_by_ilk():
    from tests.fixtures.mock_sources import MockDebtSource

    bloom = b"ALLOCATOR-BLOOM-A".ljust(32, b"\x00")
    grove_a = b"ALLOCATOR-GROVE-A".ljust(32, b"\x00")
    other = b"ALLOCATOR-OTHER-A".ljust(32, b"\x00")
    bloom_df = pd.DataFrame({"block_date": [date(2026, 7, 1)],
                             "daily_dart": [Decimal(5)], "cum_debt": [Decimal(5)]})
    grove_df = pd.DataFrame({"block_date": [date(2026, 7, 21)],
                             "daily_dart": [Decimal(1)], "cum_debt": [Decimal(1)]})
    src = MockDebtSource(bloom_df, df_by_ilk={bloom: bloom_df, grove_a: grove_df})
    assert src.debt_timeseries(bloom, date(2026, 7, 1), 1)["cum_debt"].iloc[0] == 5
    assert src.debt_timeseries(grove_a, date(2026, 7, 1), 1)["cum_debt"].iloc[0] == 1
    # Unknown ilk with a non-empty map → EMPTY (never the default frame):
    # a missing fixture must not double-count the primary ilk's debt.
    assert src.debt_timeseries(other, date(2026, 7, 1), 1).empty
    # Legacy single-ilk behaviour: empty map → default frame for any ilk.
    legacy = MockDebtSource(bloom_df)
    assert legacy.debt_timeseries(other, date(2026, 7, 1), 1)["cum_debt"].iloc[0] == 5
