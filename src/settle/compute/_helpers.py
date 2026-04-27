"""Math helpers for Compute. All inputs/outputs are `Decimal` for USD precision.

The compounding step uses `float` because `Decimal ** non-integer` raises; we
cast back to `Decimal` immediately. `float` precision (~15 digits) is plenty
for one-day APY factors.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

# Convention from RULES.md Rule 1: per-second compounding via APY.
DAYS_PER_YEAR = 365


def daily_compounding_factor(apy: Decimal) -> Decimal:
    """``(1 + APY)^(1/365) - 1`` rendered through Decimal.

    Daily interest factor for an APY-quoted rate. Used by sky_revenue and agent_rate
    per RULES.md §3 and §4.
    """
    f = (1.0 + float(apy)) ** (1.0 / DAYS_PER_YEAR) - 1.0
    return Decimal(str(f))


def cum_at_or_before(
    timeseries: pd.DataFrame,
    value_col: str,
    target: date,
    *,
    date_col: str = "block_date",
) -> Decimal:
    """Carry-forward lookup: most recent ``value_col`` whose ``date_col`` ≤ ``target``.

    Returns ``Decimal('0')`` if the timeseries is empty or has no rows ≤ target.
    Lookup is by date-max (`idxmax`), so a non-sorted DataFrame still returns
    the correct row — robustness against any source that doesn't pre-sort.
    """
    if timeseries is None or timeseries.empty:
        return Decimal("0")
    eligible = timeseries[timeseries[date_col] <= target]
    if eligible.empty:
        return Decimal("0")
    latest_idx = eligible[date_col].idxmax()
    return Decimal(str(eligible.loc[latest_idx, value_col]))


def ssr_at_or_before(ssr_history: pd.DataFrame, target: date) -> Decimal:
    """Most recent SSR APY effective on or before ``target``.

    Like ``cum_at_or_before`` but raises if no rate is at-or-before the target —
    Compute can't invent a baseline. Lookup is by date-max, not row position.
    """
    if ssr_history is None or ssr_history.empty:
        raise ValueError(f"SSR history is empty; can't determine rate for {target}")
    eligible = ssr_history[ssr_history["effective_date"] <= target]
    if eligible.empty:
        first = ssr_history["effective_date"].min()
        raise ValueError(
            f"No SSR change at or before {target}. Earliest available: {first}. "
            "Widen the SSR-history lookback in Normalize."
        )
    latest_idx = eligible["effective_date"].idxmax()
    return Decimal(str(eligible.loc[latest_idx, "ssr_apy"]))
