"""Prime-agent revenue — the prime's MtM gain on its allocation book.

Per the OBEX reconciliation pattern (`obex_monthly_pnl.sql`):

    venue_revenue = (value_eom − value_som) − period_inflow
    prime_agent_revenue = Σ venue_revenue

Where:
* ``value_X``      = ``balance_at(X) × unit_price_at(X)`` for chain block X (USD)
* ``period_inflow`` = ALM→venue underlying-token inflow during the period (USD)

A negative venue revenue means the prime spent more on inflows than the MtM grew.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pandas as pd

from ..domain.monthly_pnl import VenueRevenue
from ..domain.period import Period
from ..domain.primes import Venue
from ._helpers import cum_at_or_before


@dataclass(frozen=True, slots=True)
class VenueRevenueInputs:
    """All Compute-layer inputs needed to value one venue across `period`."""

    venue: Venue
    value_som: Decimal
    value_eom: Decimal
    inflow_timeseries: pd.DataFrame   # [block_date, daily_inflow, cum_inflow]
    # Only populated for Sky Direct venues (venue.sky_direct=True). The total
    # base-rate charge over the period: ``∫ AV(t) × apr_per_sec(t) dt``,
    # computed daily-precise by the orchestrator.
    br_charge: Decimal | None = None


def compute_venue_revenue(period: Period, inputs: VenueRevenueInputs) -> VenueRevenue:
    """One venue's contribution to prime_agent_revenue.

    For non-Sky-Direct venues: ``revenue = Δvalue − period_inflow``.

    For Sky Direct venues (per prime-settlement-methodology Step 4):
    ``revenue = max(0, ActualRev − BR_charge)`` — prime is floored at zero,
    Sky books BR_charge and absorbs the shortfall when the venue underperforms.
    """
    inflow_df = inputs.inflow_timeseries

    cum_som = cum_at_or_before(
        inflow_df, "cum_inflow", period.start - timedelta(days=1),
    )
    cum_eom = cum_at_or_before(inflow_df, "cum_inflow", period.end)
    period_inflow = cum_eom - cum_som

    actual_revenue = (inputs.value_eom - inputs.value_som) - period_inflow

    if inputs.venue.sky_direct and inputs.br_charge is not None:
        # Floored at zero: prime keeps only the surplus above BR_charge.
        revenue = max(Decimal("0"), actual_revenue - inputs.br_charge)
        sky_direct_shortfall = max(Decimal("0"), inputs.br_charge - actual_revenue)
    else:
        revenue = actual_revenue
        sky_direct_shortfall = Decimal("0")

    return VenueRevenue(
        venue_id=inputs.venue.id,
        label=inputs.venue.label,
        value_som=inputs.value_som,
        value_eom=inputs.value_eom,
        period_inflow=period_inflow,
        revenue=revenue,
        actual_revenue=actual_revenue,
        br_charge=inputs.br_charge or Decimal("0"),
        sky_direct_shortfall=sky_direct_shortfall,
    )


def compute_prime_agent_revenue(
    period: Period,
    venue_inputs: list[VenueRevenueInputs],
) -> tuple[Decimal, list[VenueRevenue]]:
    """Sum of all venue revenues (after Sky Direct floor). Returns
    ``(total, per_venue_breakdown)``. Total Sky Direct shortfall (absorbed by
    Sky) is the sum of ``vr.sky_direct_shortfall`` in the breakdown."""
    breakdown = [compute_venue_revenue(period, inp) for inp in venue_inputs]
    total = sum((vr.revenue for vr in breakdown), Decimal("0"))
    return total, breakdown
