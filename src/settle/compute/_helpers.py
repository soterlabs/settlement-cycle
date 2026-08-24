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
# on-chain accrual (`drip()` advances per `block.timestamp`). The one-day
# factor integrates the per-second factor across `SECONDS_PER_DAY` —
# mathematically identical to ``(1+APY)^(1/365)-1`` since
# ``apr_per_sec = ln(1+APY) / SECONDS_PER_YEAR``.
#
# NOTE (2026-08-24): the per-day factor was always exact, but the daily
# amounts used to be SUMMED, which charged simple interest across days.
# Accruals now compound via ``CompoundingAccrual`` below.
SECONDS_PER_DAY = 86_400
SECONDS_PER_YEAR = 365 * SECONDS_PER_DAY  # 31,536,000


def apy_to_apr_per_second(apy: Decimal) -> Decimal:
    """Convert APY to per-second APR (continuously compounded).

    ``apr_per_sec = ln(1 + APY) / SECONDS_PER_YEAR``. Matches the SSR's
    on-chain `drip()` rate convention.
    """
    apr = math.log(1.0 + float(apy))
    return Decimal(str(apr / SECONDS_PER_YEAR))


def add_spread(rate: Decimal, spread: Decimal) -> Decimal:
    """Rate plus a governance-defined spread — PLAIN ARITHMETIC addition.

    ``BR = SSR + 20 bps`` (Atlas) is a rate *definition*: the Base Rate is
    the number 20 bps above the SSR number. At SSR 3.52% the Base Rate is
    3.7200%, full stop.

    Until 2026-08-24 this composed multiplicatively
    (``(1+SSR)(1+spread) − 1``), which yielded 3.72704% — treating the
    20 bps as a second yield stacked on top of SSR. That is the right
    treatment for compounding two independent return streams on the same
    principal, but the wrong one for a defined rate, and it left Sky
    charging a 0.7040 bps (= SSR × spread) sliver it never intended:
    ``BR − SSR − 20bps`` should be exactly 0, since the whole point of the
    sUSDS spread reimbursement is that Sky nets nothing on idle sUSDS.
    Corrected per the MSC operator (2026-08-24) — worth ~−$123K/yr of Sky
    revenue at July 2026 balances.

    Used for:
      * Base rate  = SSR + spread   (30 bps; 20 bps from 2026-07-23)
      * Agent rate = SSR + 20 bps   (USDS in subproxy)

    NOTE: this is deliberately NOT a general rate-composition helper. Do
    not use it to chain genuinely independent yields (e.g. a venue APY on
    top of SSR appreciation) — those still compound multiplicatively.
    """
    return rate + spread


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


class CompoundingAccrual:
    """Daily interest accumulator where accrued interest itself earns.

    The rate conversion in ``daily_compounding_factor`` is already exact
    per-second (``(1+APY)^(1/365) ≡ ray^86400``), but *summing* those daily
    amounts charges simple interest: day d's interest is computed on the
    principal alone, never on interest accrued on days < d. Over a 31-day
    month that understates the accrual by ~0.15% of the interest (~$18.1K
    across primes in July 2026). Sky's own SSR cost compounds per-second
    on-chain, so the asymmetry favoured the primes.

    Per the MSC operator (2026-08-24) the Base Rate and SSR-derived
    accruals compound. Usage::

        acc = CompoundingAccrual()
        for day in period:
            acc.add(principal_d, daily_compounding_factor(apy_d))
        total = acc.total

    ``add`` charges ``(principal + accrued) × factor`` and returns that
    day's increment (so per-day report rows still sum to ``total``). The
    factor is the *current* day's, so a mid-period rate change applies to
    the accrued balance from that day forward.

    Scope: WITHIN one settlement period. The accrual starts fresh each
    month, and that is not a gap — the month's charge is **capitalised into
    the prime's ilk debt** at the settlement, so it compounds across months
    through the debt base instead. Allocator ilks carry a frozen
    ``vat.rate`` and no ``jug`` duty, but Sky governance calls ``vat.grab``
    with positive ``dart`` to fold accrued interest into
    ``urns[ilk][u].art`` (see the selector notes in
    ``queries/debt_timeseries.sql``), and ``cum_debt`` sums frob + grab —
    so from the settlement day the enlarged principal pays the Base Rate
    automatically, with no code here.

    Consequence: do NOT also carry an accrued-interest balance across the
    month boundary. Charging BR on unpaid interest AND on the debt minted
    to capitalise that same interest bills it twice. The only genuinely
    uncompensated window is between month-end and the settlement date
    (~20 days), which is a settlement-lag question, not a compounding one.
    """

    __slots__ = ("total",)

    def __init__(self) -> None:
        self.total = Decimal("0")

    def add(self, principal: Decimal, factor: Decimal) -> Decimal:
        """Accrue one day; returns the day's interest increment."""
        interest = (principal + self.total) * factor
        self.total += interest
        return interest


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
