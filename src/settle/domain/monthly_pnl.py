"""Result types: ``VenueRevenue`` and ``MonthlyPnL``.

Returned by ``compute_monthly_pnl``; consumed by the Load layer to render
Markdown / CSV / provenance artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .period import Month, Period
from .primes import Chain


@dataclass(frozen=True, slots=True)
class VenueRevenue:
    """Per-venue contribution to prime_agent_revenue.

    revenue = (value_eom − value_som) − period_inflow
    """

    venue_id: str
    label: str
    value_som: Decimal
    value_eom: Decimal
    period_inflow: Decimal
    revenue: Decimal


@dataclass(frozen=True, slots=True)
class MonthlyPnL:
    """Top-level result of a monthly settlement run."""

    prime_id: str
    month: Month
    period: Period

    sky_revenue: Decimal
    agent_rate: Decimal
    prime_agent_revenue: Decimal
    monthly_pnl: Decimal

    venue_breakdown: list[VenueRevenue]
    pin_blocks_som: dict[Chain, int]    # SoM blocks (= start-of-period − 1 day, EOD UTC)
    # pin_blocks_eom is on `period.pin_blocks`.

    def __post_init__(self) -> None:
        # Sanity invariant — sum holds at the Decimal level.
        expected = self.prime_agent_revenue + self.agent_rate - self.sky_revenue
        if self.monthly_pnl != expected:
            raise ValueError(
                f"monthly_pnl invariant broken: stored {self.monthly_pnl} != "
                f"expected {expected} (prime_rev + agent_rate − sky_rev)"
            )
