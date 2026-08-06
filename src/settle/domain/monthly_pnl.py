"""Result types: ``VenueRevenue`` and ``MonthlyPnL``.

Returned by ``compute_monthly_pnl``; consumed by the Load layer to render
Markdown / CSV / provenance artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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
        sd_revenue = actual_revenue × min(cap_usd, value_eom) / value_eom
                                                          (capped, EoM-locked;
                                                           see _capped_sd_revenue_eom_locked)
                     or actual_revenue × 1                (fixed, sd_share = 1)
        sd_share   = min(cap_usd, value_eom) / value_eom (capped)  | 1 (fixed)
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
    # Serialized as ``tw_avg_value_usd`` in venues.csv and provenance.json
    # (dataclass field drops the ``_usd`` suffix; serialized artifact keeps it).
    tw_avg_value: Decimal = Decimal("0")
    # When True, this venue's avg_value is excluded from the CoF allocation
    # denominator in post-hoc reporting (build_monthly_report). Mirrors the
    # same flag on Venue — propagated here so the CSV carries it forward
    # without requiring the reporting script to re-load the config YAML.
    cof_excluded: bool = False
    # ``Venue.pricing_category.value`` ("A", "B", …, "S2"). Propagated so
    # reporting can branch on venue kind (e.g. the grove-sheet renderer's
    # Savings-V2 ``deduction_avg`` path) without re-loading the config YAML
    # or inferring the kind from the sign of ``actual_revenue``.
    pricing_category: str = ""
    # When True, per-venue PnL columns (actual_revenue / revenue /
    # sd_revenue / profit_to_grove) are suppressed in display surfaces
    # (summary.md, xlsx Venues tab). Mirrors ``Venue.hide_per_venue_pnl``
    # — propagated here so renderers consume it directly from
    # provenance.json without re-loading the config YAML.
    #
    # **Display-only.** Compute code MUST NOT branch on this field —
    # the venue's ``revenue`` / ``actual_revenue`` (currently $0 for the
    # Savings V2 vaults that set this flag — position-only tracking)
    # already flow into ``MonthlyPnL.prime_agent_revenue`` via the
    # regular aggregation path.
    hide_per_venue_pnl: bool = False
    # Time-weighted average of this venue's daily lending-idle deduction
    # (prime's pro-rata share of unborrowed underlying in SparkLend / Aave
    # pools). Non-zero only for venues with ``lending_idle_usds: true``.
    # Post-hoc reporting deducts this from tw_avg_value before computing the
    # CoF-eligible average, since the idle portion is already subtracted from
    # ``utilized`` and should not carry a CoF share.
    lending_idle_tw_avg_usd: Decimal = Decimal("0")
    # Time-weighted average of the venue's off-chain notional principal,
    # for cash-distribution-only venues where the on-chain ``tw_avg_value``
    # is $0 but Sky is implicitly charging interest on the funded principal
    # (e.g. Galaxy CLO E21 = $50M off-chain loan, Anchorage tri-party).
    #
    # **Display / reconciliation-only.** Not consumed by
    # ``compute_prime_agent_revenue`` or ``compute_sky_revenue`` — headline
    # numbers (``sky_revenue``, ``prime_agent_revenue``, ``monthly_pnl``,
    # ``agent_rate``, ``sky_direct_shortfall``) are mathematically
    # independent of whether ``notional_principal_usd`` is configured on
    # any venue. Only the per-venue CoF split in
    # ``scripts/build_monthly_report.py`` reads this field (via
    # ``max(tw_avg_value, tw_avg_notional)``); Σ-totals stay exact
    # regardless. Zero when no ``notional_principal_usd`` is set.
    # Serialized as ``tw_avg_notional_usd`` in venues.csv and
    # provenance.json (same field-name convention as ``tw_avg_value``).
    tw_avg_notional: Decimal = Decimal("0")
    # 30 bps spread reimbursement deducted from Sky Revenue for
    # ``sky_savings_token: true`` Cat B venues. The prime earns SSR through
    # the sUSDS share price; Sky charges full BR then reduces its invoice by
    # this amount, making prime's net cost = SSR × V (economic neutrality).
    # Formula: value_som × (daily_compounding_factor(BASE_RATE_OVER_SSR) − 1)
    #          × n_days. Zero for all other venues.
    susds_spread_reimbursement: Decimal = Decimal("0")
    # Legacy fields kept for provenance round-trip on existing settlements
    # written under the old shortfall model. New runs always emit 0 for these.
    br_charge: Decimal = Decimal("0")
    sky_direct_shortfall: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class SDEDailyBreakdown:
    """Per-day SDE asset-value series for one venue, retained on
    ``MonthlyPnL`` so downstream reporters can render the Sky / Grove /
    in-flight decomposition without re-running RPC queries.

    The ``daily`` list mirrors the DataFrame returned by
    ``_sde_asset_value_timeseries``: one dict per period day with keys
    ``block_date`` (``date``), ``cum_value`` (``Decimal`` — the SDE-capped
    value used for utilized exclusion), and ``uncapped_value`` (``Decimal``
    — raw on-chain balance × NAV).

    The accompanying scalar fields (``burn_date`` / ``usdc_settlement_date``
    / ``end_date`` / ``cap_usd``) are the SDE-entry metadata at the moment
    of the run, copied here so the reporter can apply the phase-based
    decomposition (pre-burn → Sky+Grove, in-flight → Grove+inflight,
    settled → Grove only).
    """
    venue_id: str
    label: str
    cap_usd: Decimal | None
    burn_date: date | None
    usdc_settlement_date: date | None
    end_date: date | None
    daily: list[dict] = field(default_factory=list)


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

    # Chronicle Points — 20% of the base rate on Chronicle Farm USDS,
    # per the soterlabs/chronicle-points-dune-dash methodology. Demand-Side
    # revenue component: summed into ``prime_agent_total_revenue`` alongside
    # agent_rate + distribution_rewards. Only computed for primes whose
    # config carries a ``chronicle_points:`` block (Grove today); $0 and no
    # summary row for everyone else.
    chronicle_points: Decimal = Decimal("0")

    # Governance Accessibility Rewards — ``GarConfig.share`` (1%) of the
    # month's consolidated Sky Net Revenue (settlements/sky_total).
    # Demand-Side revenue component: summed into
    # ``prime_agent_total_revenue`` alongside agent_rate +
    # distribution_rewards. Only computed for primes whose config carries a
    # ``gar:`` block (Skybase today); $0 and no summary row for everyone
    # else.
    gar: Decimal = Decimal("0")
    # Audit string from compute/gar.py: "" (no program / pre-from_month)
    # or the share × SNR derivation, incl. the sky_total artifact's
    # generation timestamp (and a floor note when SNR was negative).
    gar_basis: str = ""

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
    # Daily-integrated SSR appreciation on the PSM3 sUSDS slice — Case 3a
    # of the sUSDS attribution fix (PRD docs/PRD_revenue_gross_net_audit.md
    # §10). The prime physically receives this growth inside its PSM3
    # share value; booked into ``prime_agent_revenue`` as a prime-level
    # addition (PSM3 is not a venue, so Σ vr.revenue < prime_agent_revenue
    # by exactly this amount for primes with a PSM3 sUSDS leg). Nets
    # against Sky's BR − 30bps = SSR collection on the same slice. Zero
    # for primes with no PSM3 leg.
    psm3_susds_appreciation: Decimal = Decimal("0")
    # Off-protocol / "tracked-but-not-counted" venues (``Venue.display_only``).
    # Surfaced for visibility in monthly reports (e.g. the dedicated xlsx tab
    # rendered by ``build_settlement_xlsx``) but NOT folded into
    # ``prime_agent_revenue``, ``sky_revenue``, or the cost-basis invariant.
    # Each entry has revenue=0 by construction; ``value_som`` / ``value_eom``
    # carry the principal currently outstanding off-protocol.
    display_only_breakdown: list[VenueRevenue] = field(default_factory=list)
    # Per-venue daily SDE asset-value series (see ``SDEDailyBreakdown``).
    # Empty list for primes / months without active SDE entries; populated
    # for every SDE venue exercised in the run. Consumed by post-hoc
    # reporters (xlsx "SDE daily" tab) to render the Sky / Grove /
    # in-flight decomposition without re-running on-chain reads.
    sde_daily_breakdown: list[SDEDailyBreakdown] = field(default_factory=list)
    # Daily Sky-revenue breakdown — one row per calendar day in the period,
    # carrying ``cum_debt`` (frob+grab), the deductions applied to derive
    # ``utilized``, the SSR / base / subsidised APYs effective on that day,
    # the subsidy ramp index ``t_months``, and the daily Sky charge
    # (actual + gross-on-cum_debt). Surfaced for the xlsx "Debt" tab so the
    # prime team can reconcile the methodology (frob vs frob+grab, APY
    # composition, subsidy ramp) without re-running the pipeline. Each entry
    # is a dict with stringified Decimals + float APYs — same wire-format
    # convention as ``SDEDailyBreakdown.daily``.
    sky_revenue_daily: list[dict] = field(default_factory=list)
    # Total 30 bps spread deducted from ``sky_revenue`` for all
    # ``sky_savings_token`` Cat B venues (= Σ vr.susds_spread_reimbursement
    # across the venue breakdown). Replaces the prior Prime-Revenue-credit
    # semantics (PR ``fix/susds-methodology``): Sky now charges full BR on
    # utilized, then reduces its invoice by this aggregate — prime's net
    # cost matches SSR × V (economic neutrality). ``sky_revenue`` is
    # already net of this deduction; field surfaced for audit /
    # reconciliation. Zero for primes with no sky_savings_token Cat B
    # venues.
    susds_spread_reimbursement: Decimal = Decimal("0")
    # Pure BR × full ilk debt with NO deductions: no idle-USDS, no PSM,
    # no SDE asset-value, no Curve/lending idle. Uses the same subsidised
    # BR + ramp schedule as the actual ``sky_revenue``. Display-only —
    # NOT the gross analog of ``sky_revenue`` (which also adds the SDE
    # actual revenue on top of BR-on-utilized and subtracts the sUSDS
    # spread reimbursement). The two relate as:
    #
    #     sky_revenue_gross = Σ_d subsidised_BR × cum_debt_d
    #     sky_revenue       = Σ_d subsidised_BR × utilized_d  +  sde_revenue
    #                         − susds_spread_reimbursement
    #
    # so for primes with active SDE positions, ``sky_revenue`` can EXCEED
    # ``sky_revenue_gross`` (when SDE revenue > the deductions' BR cost
    # plus the susds spread reimbursement).
    #
    # The field is consumed by ``build_monthly_report.py`` to display
    # "BR reduction from idle/SDE deductions" as ``sky_revenue_gross
    # − cof_total``. Not part of any settlement invariant. Zero default
    # for backward compat with old provenance.
    sky_revenue_gross: Decimal = Decimal("0")

    # Per-period subsidised-borrowing aggregates (``compute.sky_revenue.
    # summarize_subsidy``): time-weighted utilized, $1B tranche split,
    # base/ref/effective rates, the subsidy's $ benefit vs full base rate,
    # and a zero-benefit flag. ``None`` for primes without a subsidy. This is
    # the single source the xlsx "Rates & subsidy" panel formats — the panel
    # does no economics of its own, so it cannot drift from what was charged.
    subsidy_summary: dict | None = None

    # Distribution Rewards per ref code for the period, from the
    # ``settle-dr-dune`` reconciliation workbook (Summary tab). Each entry is
    # ``{"ref_code", "amount" (Decimal), "notes"}``. Their sum equals
    # ``distribution_rewards``. Empty for primes without tagged DR. Surfaced
    # for the summary.md "DR per ref code" table; populated at report-write
    # time by ``settle.load.dr_rewards.enrich_with_dr``.
    dr_breakdown: list[dict] = field(default_factory=list)

    @property
    def prime_agent_total_revenue(self) -> Decimal:
        """Sum of all revenue streams to the prime — the reported headline.

        ``= prime_agent_revenue + agent_rate + distribution_rewards
           + chronicle_points + gar``

        Note: per-venue ``external_revenue`` (off-pool rewards like Merkl
        drops on aTokens) is already folded into ``prime_agent_revenue``
        via ``vr.revenue`` in the per-venue rollup — this property doesn't
        add it again.
        """
        return (
            self.prime_agent_revenue
            + self.agent_rate
            + self.distribution_rewards
            + self.chronicle_points
            + self.gar
        )

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
            + self.chronicle_points
            + self.gar
            - self.sky_revenue
        )
        if self.monthly_pnl != expected:
            raise ValueError(
                f"monthly_pnl invariant broken: stored {self.monthly_pnl} != "
                f"expected {expected} (prime_rev + agent_rate + "
                "distribution_rewards + chronicle_points + gar − sky_rev)"
            )
