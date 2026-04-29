"""Math helpers for Compute. All inputs/outputs are `Decimal` for USD precision.

The compounding step uses `float` because `Decimal ** non-integer` raises; we
cast back to `Decimal` immediately. `float` precision (~15 digits) is plenty
for one-day rate factors.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pandas as pd

# All rates compound at per-second APR — same granularity as the SSR's
# on-chain accrual (`drip()` advances per `block.timestamp`). Daily summing
# (PRD §17.5) integrates the per-second factor across `SECONDS_PER_DAY` —
# mathematically identical to ``(1+APY)^(1/365)-1`` since
# ``apr_per_sec = ln(1+APY) / SECONDS_PER_YEAR``.
SECONDS_PER_DAY = 86_400
SECONDS_PER_YEAR = 365 * SECONDS_PER_DAY  # 31,536,000


def apy_to_apr_per_second(apy: Decimal) -> Decimal:
    """Convert APY to per-second APR (continuously compounded).

    ``apr_per_sec = ln(1 + APY) / SECONDS_PER_YEAR``. Matches the SSR's
    on-chain `drip()` rate convention.
    """
    apr = math.log(1.0 + float(apy))
    return Decimal(str(apr / SECONDS_PER_YEAR))


def daily_compounding_factor(apy: Decimal) -> Decimal:
    """One-day growth factor for an APY-quoted rate.

    Implemented as the closed form ``(1+APY)^(1/365) − 1`` — mathematically
    identical (modulo float rounding) to integrating the per-second APR over
    one day: ``e^(ln(1+APY) / SECONDS_PER_YEAR × SECONDS_PER_DAY) − 1``. The
    per-second framing is the conceptual source of truth (matches SSR's
    on-chain ``drip()`` semantics); the closed form is kept here because it
    avoids two intermediate float conversions.
    """
    f = (1.0 + float(apy)) ** (1.0 / 365) - 1.0
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

    Note: returning ``0`` on empty is the correct default for *flow* timeseries
    (inflow / per-venue activity) where "no rows" genuinely means "no activity".
    For required scalars (cum_debt, SSR), use ``require_non_empty`` first to
    fail loudly on a misconfigured source instead of silently zeroing out.
    """
    if timeseries is None or timeseries.empty:
        return Decimal("0")
    eligible = timeseries[timeseries[date_col] <= target]
    if eligible.empty:
        return Decimal("0")
    latest_idx = eligible[date_col].idxmax()
    return Decimal(str(eligible.loc[latest_idx, value_col]))


def require_non_empty(timeseries: pd.DataFrame, *, name: str, hint: str = "") -> None:
    """Raise ``ValueError`` if ``timeseries`` is None or has zero rows.

    Use for inputs whose emptiness almost certainly signals a misconfigured
    source (wrong ``ilk_bytes32``, Dune query failure, missing fixture) rather
    than legitimate "no activity". The downstream ``cum_at_or_before`` would
    silently return ``0`` and the run would complete with materially wrong
    numbers — this guard turns that failure mode into a loud crash.
    """
    if timeseries is None or len(timeseries) == 0:
        raise ValueError(
            f"{name} timeseries is empty — likely a misconfigured source. "
            + (hint or "Check the prime config and Source implementation.")
        )


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
