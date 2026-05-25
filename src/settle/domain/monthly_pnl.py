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
        revenue = actual_revenue + external_revenue (everything to prime)
        sd_share = 0, sd_revenue = 0

    For SDE venues (Step 4 of prime-settlement-methodology, kind=fixed|capped):
        actual_revenue = (value_eom − value_som) − period_inflow
        sd_revenue = Σ_d daily_rev_d × sd_share_d        (capped, daily snapshots)
                     or actual_revenue × 1               (fixed, sd_share = 1)
        sd_share   = sd_revenue / actual_revenue         (effective avg, display only)
        revenue = actual_revenue − sd_revenue + external_revenue   (to prime)

    The SDE position's asset value is also excluded from the prime's
    utilized-USDS BR base — handled by the orchestrator passing the
    daily SDE-asset-value timeseries into ``compute_sky_revenue``.

    ``external_revenue`` is a separate revenue stream that is NOT subject to
    SDE-splitting — see field doc below. Used today by the Cat C aToken path
    to credit Merkl-style aToken drops from allowlisted senders.
    """

    venue_id: str
    label: str
    value_som: Decimal
    value_eom: Decimal
    period_inflow: Decimal
    revenue: Decimal                            # to prime (after SDE split + external_revenue)
    actual_revenue: Decimal = Decimal("0")      # whole-venue (pre-split, EXCLUDES external_revenue)
    sd_share: Decimal = Decimal("0")            # effective avg = sd_revenue/actual_revenue; 0 = non-SDE; 1 = fixed SDE
    sd_revenue: Decimal = Decimal("0")          # to Sky from this venue (= actual × sd_share)
    # External rewards received from `prime.external_alm_sources` addresses
    # for THIS venue's token during the period. Always goes 100 % to prime
    # (not subject to SDE-splitting): the SDE deal terms cover the pool-
    # native yield, not off-pool reward distributions (Merkl, Anchorage,
    # etc.). Computed in USD; zero for venues whose pricing category has no
    # external-rewards path wired up yet. See `normalize.positions.
    # _atoken_external_revenue_usd` for the Cat C implementation.
    external_revenue: Decimal = Decimal("0")
    # Time-weighted average principal across the period:
    #   tw_avg = mean(value_som + cum_inflow_d for d in period.start..end)
    # Used by post-hoc reporting (build_monthly_report, build_settlement_xlsx)
    # to allocate the CoF charge across venues. SoM/EoM averaging mis-states
    # this materially when inflows are concentrated mid-month — see
    # ``_time_weighted_avg_value`` in compute.prime_agent_revenue.
    tw_avg_value: Decimal = Decimal("0")
    # When True, this venue's avg_value is excluded from the CoF allocation
    # denominator in post-hoc reporting (build_monthly_report). Mirrors the
    # same flag on Venue — propagated here so the CSV carries it forward
    # without requiring the reporting script to re-load the config YAML.
    cof_excluded: bool = False
    # Time-weighted average of this venue's daily lending-idle deduction
    # (prime's pro-rata share of unborrowed underlying in SparkLend / Aave
    # pools). Non-zero only for venues with ``lending_idle_usds: true``.
    # Post-hoc reporting deducts this from tw_avg_value before computing the
    # CoF-eligible average, since the idle portion is already subtracted from
    # ``utilized`` and should not carry a CoF share.
    lending_idle_tw_avg_usd: Decimal = Decimal("0")
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
    # 30 bps spread Prime Revenue on sUSDS held inside Curve LP pools — added
    # to ``prime_agent_revenue`` outside the venue loop (PRD §17.11). Surfaced
    # here so downstream reporting can attribute the missing revenue back
    # (without it, Σ vr.revenue < prime_agent_revenue for any prime holding
    # sUSDS in a Curve pool). Zero for primes with no such positions.
    curve_susds_spread: Decimal = Decimal("0")
    # 30 bps spread Prime Revenue on the sUSDS slice of PSM3 holdings — same
    # shape, different source (PSM3 daily totals, not venue rows). Zero for
    # primes with no PSM3 leg configured.
    psm3_susds_spread: Decimal = Decimal("0")

    @property
    def prime_agent_total_revenue(self) -> Decimal:
        """Sum of all revenue streams to the prime — the reported headline.

        ``= prime_agent_revenue + agent_rate + distribution_rewards``

        Note: per-venue ``external_revenue`` (off-pool rewards like Merkl
        drops on aTokens) is already folded into ``prime_agent_revenue``
        via ``vr.revenue`` in the per-venue rollup — this property doesn't
        add it again.
        """
        return self.prime_agent_revenue + self.agent_rate + self.distribution_rewards

    def __post_init__(self) -> None:
        # Sanity invariant — sum holds at the Decimal level. Kept (per design
        # decision) even though monthly_pnl isn't a reported number; serves as
        # a cross-check that the components add up consistently. Includes
        # ``distribution_rewards`` so the invariant stays correct once that
        # field is populated in Phase 3+. ``external_revenue`` is summed into
        # ``prime_agent_revenue`` upstream (per-venue) so it doesn't appear
        # explicitly here.
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
