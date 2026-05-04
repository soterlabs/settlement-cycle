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
from ..domain.sde import SDEEntry
from ._helpers import cum_at_or_before


@dataclass(frozen=True, slots=True)
class VenueRevenueInputs:
    """All Compute-layer inputs needed to value one venue across `period`."""

    venue: Venue
    value_som: Decimal
    value_eom: Decimal
    inflow_timeseries: pd.DataFrame   # [block_date, daily_inflow, cum_inflow]
    # Set when the venue is in an active SDE entry (kind=fixed or capped).
    # None means the venue is not Sky-Direct → all revenue to prime.
    sde_entry: SDEEntry | None = None


def _sd_share_at_som(
    sde_entry: SDEEntry | None, value_som: Decimal,
) -> Decimal:
    """Sky-direct slice as a fraction of value_som (0 for non-SDE; 1 for
    fixed; ``min(cap, value_som) / value_som`` for capped, locked at SoM).
    """
    if sde_entry is None:
        return Decimal("0")
    if sde_entry.kind == "fixed":
        return Decimal("1")
    if sde_entry.kind == "capped" and value_som > 0:
        return min(sde_entry.cap_usd, value_som) / value_som
    return Decimal("0")


def compute_venue_revenue(period: Period, inputs: VenueRevenueInputs) -> VenueRevenue:
    """One venue's contribution to prime_agent_revenue under the SDE-split model.

    actual_revenue = (value_eom − value_som) − period_inflow
    sd_share       = (set per SDE entry; 0 for non-SDE)
    sd_revenue     = actual_revenue × sd_share        (to Sky)
    revenue        = actual_revenue × (1 − sd_share)  (to prime)

    Loss handling: a negative actual_revenue is split the same way — Sky
    absorbs sd_share of the loss, prime absorbs the rest. This matches Grove
    team's PnL workbook (no floor, no shortfall).
    """
    inflow_df = inputs.inflow_timeseries

    cum_som = cum_at_or_before(
        inflow_df, "cum_inflow", period.start - timedelta(days=1),
    )
    cum_eom = cum_at_or_before(inflow_df, "cum_inflow", period.end)
    period_inflow = cum_eom - cum_som

    actual_revenue = (inputs.value_eom - inputs.value_som) - period_inflow
    sd_share = _sd_share_at_som(inputs.sde_entry, inputs.value_som)
    sd_revenue = actual_revenue * sd_share
    prime_revenue = actual_revenue - sd_revenue

    return VenueRevenue(
        venue_id=inputs.venue.id,
        label=inputs.venue.label,
        value_som=inputs.value_som,
        value_eom=inputs.value_eom,
        period_inflow=period_inflow,
        revenue=prime_revenue,
        actual_revenue=actual_revenue,
        sd_share=sd_share,
        sd_revenue=sd_revenue,
    )


def compute_prime_agent_revenue(
    period: Period,
    venue_inputs: list[VenueRevenueInputs],
) -> tuple[Decimal, list[VenueRevenue]]:
    """Sum of prime-side venue revenue (= Σ actual × (1 − sd_share)).

    Returns ``(total, per_venue_breakdown)``. Sky's claim from SDE positions
    is the sum of ``vr.sd_revenue`` in the breakdown, added to sky_revenue
    by the orchestrator.
    """
    breakdown = [compute_venue_revenue(period, inp) for inp in venue_inputs]
    total = sum((vr.revenue for vr in breakdown), Decimal("0"))
    return total, breakdown
