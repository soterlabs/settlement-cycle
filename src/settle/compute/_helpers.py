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

# TWO daily factors live here, and which one applies is a property of the
# RATE, not of the caller (2026-09-01; see PRD §17.13 and docs/RULES.md):
#
#   * ``apr_daily`` — for NOMINAL rates: the Base Rate, the agent rate, the
#     20 bps reimbursement legs, Chronicle Points. An APR's compounding
#     happens when the MSC capitalises the charge into the ilk debt, not
#     inside the settlement period.
#   * ``daily_compounding_factor`` — for APY-quoted rates, i.e. the
#     SSR-appreciation legs, whose per-second growth the sUSDS index really
#     does deliver.
#
# NEITHER is accumulated with interest-on-interest inside a period. For the
# nominal legs that would contradict the APR definition; for the SSR legs
# it would DOUBLE-COUNT, because their principal is already mark-to-market
# (re-read via ``convertToAssets`` daily, so it carries the compounding
# already). See ``monthly_pnl._accrue_daily``.
SECONDS_PER_DAY = 86_400
SECONDS_PER_YEAR = 365 * SECONDS_PER_DAY  # 31,536,000


def apy_to_apr_per_second(apy: Decimal) -> Decimal:
    """Convert APY to per-second APR (continuously compounded).

    ``apr_per_sec = ln(1 + APY) / SECONDS_PER_YEAR``. Matches the SSR's
    on-chain `drip()` rate convention.
    """
    apr = math.log(1.0 + float(apy))
    return Decimal(str(apr / SECONDS_PER_YEAR))


# Compounding frequency used to convert an APY into an APR. The MSC settles
# monthly and the charge is capitalised into the prime's ilk debt at each
# settlement (``vat.grab`` positive dart), so the accrual compounds ~12x a
# year. Converting at the SAME frequency makes the round trip exact:
# ``(1 + apy_to_apr(APY)/12)^12 - 1 == APY``. Converting at a different
# frequency (e.g. per-second, which yields ln(1+APY)) leaves a residual —
# 0.52 bps/yr at SSR 3.52% — because the conversion would assume continuous
# compounding the settlement cycle doesn't provide.
APR_COMPOUNDING_PERIODS = 12


def apy_to_apr(apy: Decimal, n: int = APR_COMPOUNDING_PERIODS) -> Decimal:
    """Convert an APY-quoted rate to its APR (nominal) equivalent.

    ``APR = n x [(1 + APY)^(1/n) - 1]``

    Why this exists: the Base Rate is ``SSR + spread``, and the two are
    quoted in different units. SSR is an APY (it compounds per-second into
    the sUSDS index on-chain); the spread is a governance-set APR. Adding
    them directly mixes an effective rate with a nominal one. Converting
    SSR to an APR first puts both on the nominal basis, and then plain
    addition is exact AT THE RATE LEVEL: ``BR_apr - SSR_apr - spread = 0``.

    CAVEAT — that identity does not carry into settled dollars, because the idle-sUSDS netting is NOT exact: the
    SSR the charge bills (``SSR_apr/365``) is 0.48 bps/yr above the SSR the
    appreciation legs credit (``(1+SSR)^(1/365)-1``, which tracks the index).
    That is the unavoidable cost of n=12 — see the trade-off table in PRD
    §17.13.

    At SSR 3.52% and n=12 this gives 3.464456%, so ``BR_apr`` = 3.664456%.

    (Supersedes the former APY composition — multiplicative
    ``(1+SSR)(1+spread)-1`` originally, briefly additive-on-APYs. Both
    mixed an effective rate with a nominal one. See PRD SS17.13.)
    """
    if n < 1:
        raise ValueError(f"apy_to_apr: n must be >= 1, got {n}")
    # ``expm1(log1p(x)/n)`` rather than ``(1+x)**(1/n) - 1``: the latter
    # computes a number just above 1.0 and then subtracts 1, throwing away
    # most of the mantissa. At n = 12 both are fine, but at per-second n the
    # naive form loses ~9 significant digits (it disagreed with the exact
    # series expansion at the 9th decimal instead of the 11th).
    f = math.expm1(math.log1p(float(apy)) / n)
    return Decimal(str(f)) * Decimal(n)


def apr_daily(apr: Decimal, days: int = 1) -> Decimal:
    """Interest factor for ``days`` at a nominal annual rate — ``apr x days/365``.

    NOMINAL means no compounding inside the settlement period: an APR is
    defined by its periodic rate, and the compounding that does occur
    happens at the MSC when the charge is capitalised into the ilk debt.
    Contrast ``daily_compounding_factor``, which is the APY equivalent and
    is still used for the SSR-appreciation legs (the sUSDS index genuinely
    compounds per-second, so those model a physical receipt).
    """
    return apr * Decimal(days) / Decimal(365)


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

    Returns ``Decimal('0')`` if the timeseries is empty, has no rows ≤ target,
    or doesn't have ``value_col``. The missing-column fallback exists so that
    new per-leg PSM columns (``cum_usds_leg``, ``cum_usdc``, ``cum_susds``)
    degrade gracefully when consumers are handed an older-shape frame —
    pre-PSM3-leg-split test fixtures, in particular.

    Lookup is by date-max, so a non-sorted DataFrame still returns the
    correct row — robustness against any source that doesn't pre-sort.
    Among rows TIED on the max date, the positionally LAST one wins: for
    a cumulative series with multiple same-day rows (per-event aToken
    inflows), the last row carries the end-of-day cumulative. (The old
    ``idxmax`` lookup took the FIRST tied row, silently dropping every
    later same-day event — E3 April 2026 lost a $1.41M Merkl round-trip
    this way.) Producers should still emit one row per date; this is
    defence in depth.

    Note: returning ``0`` on empty is the correct default for *flow* timeseries
    (inflow / per-venue activity) where "no rows" genuinely means "no activity".
    For required scalars (cum_debt, SSR), use ``require_non_empty`` first to
    fail loudly on a misconfigured source instead of silently zeroing out.
    """
    if timeseries is None or timeseries.empty:
        return Decimal("0")
    if value_col not in timeseries.columns:
        return Decimal("0")
    eligible = timeseries[timeseries[date_col] <= target]
    if eligible.empty:
        return Decimal("0")
    tied = eligible[eligible[date_col] == eligible[date_col].max()]
    return Decimal(str(tied[value_col].iloc[-1]))


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
