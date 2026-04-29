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

    For non-Sky-Direct venues:
        actual_revenue = (value_eom − value_som) − period_inflow
        revenue = actual_revenue
        br_charge = 0,  sky_direct_shortfall = 0

    For Sky Direct venues (Step 4 of prime-settlement-methodology):
        actual_revenue = (value_eom − value_som) − period_inflow
        revenue = max(0, actual_revenue − br_charge)
        sky_direct_shortfall = max(0, br_charge − actual_revenue)
    """

    venue_id: str
    label: str
    value_som: Decimal
    value_eom: Decimal
    period_inflow: Decimal
    revenue: Decimal
    # New fields for Sky Direct accounting. Default to zero / actual_revenue
    # for non-Sky-Direct venues so the dataclass stays drop-in compatible.
    actual_revenue: Decimal = Decimal("0")
    br_charge: Decimal = Decimal("0")
    sky_direct_shortfall: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class MonthlyPnL:
    """Top-level result of a monthly settlement run.

    The reported headline is **prime_agent_total_revenue** and **sky_revenue**,
    not the netted ``monthly_pnl``. ``monthly_pnl`` stays computed for audit
    (provenance.json) but is omitted from markdown headline + pnl.csv.
    """

    prime_id: str
    month: Month
    period: Period

    sky_revenue: Decimal
    agent_rate: Decimal
    prime_agent_revenue: Decimal
    monthly_pnl: Decimal     # kept for invariant + audit (provenance.json); not reported in headline / CSV

    venue_breakdown: list[VenueRevenue]
    pin_blocks_som: dict[Chain, int]
    # pin_blocks_eom is on `period.pin_blocks`.

    # Default-zero placeholder. Populated when a distribution-rewards source
    # lands (referral codes — see skybase). Always summed into
    # prime_agent_total_revenue so the headline structure stays stable as
    # this rolls out.
    distribution_rewards: Decimal = Decimal("0")

    # Total base-rate shortfall absorbed by Sky on Sky Direct venues
    # (= Σ max(0, BR_charge_v − ActualRev_v) over sky_direct=True venues).
    # Sky books BR_charge as revenue but gives up the shortfall — so this
    # amount is subtracted from sky_revenue to get Sky's actual cash claim.
    sky_direct_shortfall: Decimal = Decimal("0")

    @property
    def prime_agent_total_revenue(self) -> Decimal:
        """Sum of all revenue streams to the prime — the reported headline.

        ``= prime_agent_revenue + agent_rate + distribution_rewards``
        """
        return self.prime_agent_revenue + self.agent_rate + self.distribution_rewards

    def __post_init__(self) -> None:
        # Sanity invariant — sum holds at the Decimal level. Kept (per design
        # decision) even though monthly_pnl isn't a reported number; serves as
        # a cross-check that the components add up consistently. Includes
        # ``distribution_rewards`` so the invariant stays correct once that
        # field is populated in Phase 3+.
        expected = (
            self.prime_agent_revenue
            + self.agent_rate
            + self.distribution_rewards
            - self.sky_revenue
        )
        if self.monthly_pnl != expected:
            raise ValueError(
                f"monthly_pnl invariant broken: stored {self.monthly_pnl} != "
                f"expected {expected} (prime_rev + agent_rate + distribution_rewards − sky_rev)"
            )
