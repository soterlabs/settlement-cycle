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

    For non-SDE venues:
        actual_revenue = (value_eom − value_som) − period_inflow
        revenue = actual_revenue (everything to prime)
        sd_share = 0, sd_revenue = 0

    For SDE venues (Step 4 of prime-settlement-methodology, kind=fixed|capped):
        actual_revenue = (value_eom − value_som) − period_inflow
        sd_share = min(cap_usd, value_som) / value_som   (= 1 for kind=fixed)
        sd_revenue = actual_revenue × sd_share           (flows to Sky)
        revenue = actual_revenue × (1 − sd_share)        (flows to prime)

    The SDE position's asset value is also excluded from the prime's
    utilized-USDS BR base — handled by the orchestrator passing the
    daily SDE-asset-value timeseries into ``compute_sky_revenue``.
    """

    venue_id: str
    label: str
    value_som: Decimal
    value_eom: Decimal
    period_inflow: Decimal
    revenue: Decimal                            # to prime (after SDE split)
    actual_revenue: Decimal = Decimal("0")      # whole-venue (pre-split)
    sd_share: Decimal = Decimal("0")            # 0 = non-SDE; 1 = full SDE; in (0,1) = capped
    sd_revenue: Decimal = Decimal("0")          # to Sky from this venue (= actual × sd_share)
    # Legacy fields kept for provenance round-trip on existing settlements
    # written under the old shortfall model. New runs always emit 0 for these.
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

    # Legacy: kept for provenance round-trip. Always 0 under the SDE-config
    # model (Sky takes actual SDE revenue; no floor → no shortfall).
    sky_direct_shortfall: Decimal = Decimal("0")
    # Sum of SDE revenue across the breakdown (=Σ vr.sd_revenue). Already
    # included in sky_revenue; reported separately for transparency.
    sde_revenue: Decimal = Decimal("0")

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
