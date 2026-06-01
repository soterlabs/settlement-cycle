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
from ..domain.primes import NotionalScheduleEntry, Venue
from ..domain.sde import SDEEntry
from ._helpers import cum_at_or_before


def _time_weighted_notional(
    schedule: tuple[NotionalScheduleEntry, ...] | None,
    period: Period,
) -> Decimal:
    """Time-weighted average of a venue's off-chain notional principal.

    ``schedule`` is sorted ascending by ``start_date``; each entry sets the
    notional from its ``start_date`` onward (step function). The function
    averages the daily-applicable notional across ``[period.start,
    period.end]``. Returns ``Decimal("0")`` when ``schedule`` is None or
    empty (no notional configured for this venue).
    """
    if schedule is None or len(schedule) == 0:
        return Decimal("0")
    n_days = period.n_days
    if n_days <= 0:
        return Decimal("0")
    sorted_schedule = sorted(schedule, key=lambda e: e.start_date)
    total = Decimal("0")
    d = period.start
    while d <= period.end:
        applicable = Decimal("0")
        for entry in sorted_schedule:
            # ``sorted_schedule`` is ascending by ``start_date``; once we hit
            # an entry that hasn't activated yet we know all later entries
            # also haven't, so the last-set ``applicable`` is final for ``d``.
            if entry.start_date <= d:
                applicable = entry.amount
            else:
                break
        total += applicable
        d += timedelta(days=1)
    return total / Decimal(n_days)


def _time_weighted_avg_value(
    period: Period,
    value_som: Decimal,
    inflow_timeseries: pd.DataFrame,
) -> Decimal:
    """Mean of daily principal across the period.

        daily_position_d = value_som + (cum_inflow_d − cum_inflow_{som-1})
        tw_avg = Σ_{d=start..end} daily_position_d / n_days

    Captures the time-shape of principal flows (mid-month deposits and
    withdrawals) that the simple ``(value_som + value_eom) / 2`` misses.
    Downstream reporting uses this to allocate the CoF charge across venues
    — a $300M deposit on day 28 of a 30-day month has a time-weighted avg
    of ~$38M (not $150M), so its CoF share is 4× smaller than SoM/EoM avg
    would suggest. Σ-totals are unaffected; only per-venue split changes.

    Yield-driven token-price drift within the period is treated as
    negligible (~0.3–0.7% / month at Sky-relevant rates) — only principal
    flows are reflected here. Returns ``value_som`` if there are no
    inflow rows (degenerate case: stable position throughout).
    """
    n_days = period.n_days
    if n_days <= 0:
        return value_som
    if inflow_timeseries is None or inflow_timeseries.empty:
        return value_som
    cum_baseline = cum_at_or_before(
        inflow_timeseries, "cum_inflow", period.start - timedelta(days=1),
    )
    total = Decimal("0")
    d = period.start
    while d <= period.end:
        cum_d = cum_at_or_before(inflow_timeseries, "cum_inflow", d)
        total += value_som + (cum_d - cum_baseline)
        d += timedelta(days=1)
    return total / Decimal(n_days)


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
    # When set, bypasses the (value_eom − value_som) − period_inflow formula
    # and uses this value directly as actual_revenue. Used for yield-bearing
    # ERC-4626 tokens at the ALM (e.g. sUSDS POL) where the SSR appreciation
    # flows back to Sky via the borrow-rate charge and only the 30bps spread
    # (BR − SSR) is Prime Revenue.
    actual_revenue_override: Decimal | None = None
    # USD value of external rewards arriving at the ALM for this venue's
    # token during the period — yield delivered off-pool (Merkl drops,
    # Anchorage sweeps, BUIDL yield mints) that the closed-form per-venue
    # revenue formula does NOT capture. Flows 100% to the prime (not
    # SDE-split) since these are typically outside the SDE deal terms.
    # Zero for venues whose pricing category has no external-rewards path
    # wired up yet (today: Cat C aTokens only).
    external_revenue: Decimal = Decimal("0")
    # For ERC-4626 Centrifuge venues: the exact period inflow derived from
    # on-chain Deposit/Withdraw event ``assets`` amounts (USDC exact). When
    # set, this overrides the ``period_inflow`` displayed in the output and
    # the ``actual_revenue`` formula. Under the EoM-locked capped-sd_share
    # methodology the vault-event actual_revenue carries the full intra-epoch
    # yield naturally, so no separate intra-epoch share is needed.
    erc4626_period_inflow: "Decimal | None" = None


def _sd_share_at_som(
    sde_entry: SDEEntry | None, value_som: Decimal,
) -> Decimal:
    """Sky-direct slice as a fraction of value_som (0 for non-SDE; 1 for
    fixed; ``min(cap, value_som) / value_som`` for capped, locked at SoM).
    Used as the fallback when no daily timeseries is available.
    """
    if sde_entry is None:
        return Decimal("0")
    if sde_entry.kind == "fixed":
        return Decimal("1")
    if sde_entry.kind == "capped" and value_som > 0:
        return min(sde_entry.cap_usd, value_som) / value_som
    return Decimal("0")


def _capped_sd_revenue_eom_locked(
    cap_usd: Decimal,
    value_som: Decimal,
    value_eom: Decimal,
    actual_revenue: Decimal,
) -> Decimal:
    """sd_revenue = actual_revenue × min(cap_usd, value_eom) / value_eom.

    EoM-locked sd_share: one snapshot at period end, applied to the full
    actual_revenue. Matches Grove team's PnL workbook methodology — see
    PRD §17.13 / 2026-05-28 comparison vs Grove Jan–Apr 2026 data.

    **Why EoM-locked rather than daily-resolved?** A daily-resolved approach
    (Σ daily_rev_d × sd_share_d, with sd_share_d = min(cap, v_d) / v_d) is
    in principle more granular, but it diverges from Grove's reporting
    whenever the position moves materially mid-period — Grove's workbook
    consistently uses the period-end cap ratio. For a stable position
    (Feb 2026: $454M throughout) the two methods agree. For a moving
    position (Jan 2026 E8: $751M → $454M mid-month) they diverge by ~13
    percentage points; EoM-locked is the empirical fit.

    **Burn day handling.** When a capped tranche is destroyed on-chain
    mid-period (e.g., Grove E8 JAAA Mar 9: Sky's $325M tranche burned;
    Grove's $128M slice survived), EoM-locked yields ``sd_share = 1.0``
    automatically (cap > value_eom), which empirically attributes ~100%
    of the period's net P&L to Sky — close enough to Grove's −$451K out
    of −$458K total (98.5%) for the Mar 2026 case. The SDE entry's
    ``burn_date`` field (introduced earlier for the daily-resolved path)
    is kept on ``SDEEntry`` for documentation but is no longer consumed
    here; the EoM snapshot naturally absorbs the burn.

    **Full-redemption degenerate case (value_eom = 0).** Under EoM-locked the
    ratio min(cap, 0)/0 is undefined. Fall back to the SoM-locked share
    (``min(cap, value_som) / value_som``) — the snapshot at the *other* end
    of the period — so a tranche fully redeemed during the period still
    attributes a defensible Sky / Prime split rather than silently dropping
    the entire loss on Prime. Returns 0 only when both endpoints are 0.
    """
    if value_eom > 0:
        sd_share = min(cap_usd, value_eom) / value_eom
        return actual_revenue * sd_share
    if value_som > 0:
        sd_share = min(cap_usd, value_som) / value_som
        return actual_revenue * sd_share
    return Decimal("0")


def compute_venue_revenue(period: Period, inputs: VenueRevenueInputs) -> VenueRevenue:
    """One venue's contribution to prime_agent_revenue under the SDE-split model.

    actual_revenue   = (value_eom − value_som) − period_inflow
    sd_revenue       = actual_revenue × sd_share
    sd_share         = min(cap, value_eom) / value_eom    (EoM-locked, capped SDE)
                       1                                  (fixed SDE)
                       0                                  (non-SDE)
    prime_revenue    = actual_revenue − sd_revenue + external_revenue

    Capped SDE uses the EoM-locked share — see ``_capped_sd_revenue_eom_locked``
    for the methodology rationale (matches Grove team's PnL workbook), including
    the burn-day and full-redemption fallbacks. Fixed SDE has ``sd_share = 1``.

    The ``external_revenue`` stream — off-pool rewards (Merkl, Anchorage,
    etc.) — is added AFTER the SDE split because it doesn't belong to the
    Sky-Direct deal terms (the SDE covers pool-native yield only). For a
    non-SDE venue this is equivalent to ``actual_revenue + external_revenue``.

    Loss handling: a negative actual_revenue is split the same way — Sky
    absorbs sd_share of the loss, prime absorbs the rest. This matches Grove
    team's PnL workbook (no floor, no shortfall).
    """
    if inputs.actual_revenue_override is not None:
        actual_revenue = inputs.actual_revenue_override
        period_inflow = Decimal("0")
        # Override venues (sUSDS spread at ALM) have a stable position
        # roughly at value_som; the simple value_som is the best avg
        # available without a daily series.
        tw_avg_value = inputs.value_som
    elif inputs.erc4626_period_inflow is not None:
        # ERC-4626 Centrifuge venues: use exact vault-event USDC amounts for
        # the period inflow and revenue formula. With EoM-locked sd_share
        # (see ``_capped_sd_revenue_eom_locked``) the cap split applies
        # uniformly to the full vault-event actual_revenue, so no separate
        # token-transfer-clock decomposition is needed here.
        period_inflow = inputs.erc4626_period_inflow
        actual_revenue = (inputs.value_eom - inputs.value_som) - period_inflow
        inflow_df = inputs.inflow_timeseries
        tw_avg_value = _time_weighted_avg_value(
            period, inputs.value_som, inflow_df,
        )
    else:
        inflow_df = inputs.inflow_timeseries
        cum_som = cum_at_or_before(
            inflow_df, "cum_inflow", period.start - timedelta(days=1),
        )
        cum_eom = cum_at_or_before(inflow_df, "cum_inflow", period.end)
        period_inflow = cum_eom - cum_som
        actual_revenue = (inputs.value_eom - inputs.value_som) - period_inflow
        tw_avg_value = _time_weighted_avg_value(
            period, inputs.value_som, inflow_df,
        )

    entry = inputs.sde_entry
    if entry is not None and entry.kind == "capped":
        sd_revenue = _capped_sd_revenue_eom_locked(
            entry.cap_usd, inputs.value_som, inputs.value_eom, actual_revenue,
        )
        # Display sd_share. Use the EoM-locked theoretical share whenever a
        # position exists (so a break-even capped period still reports e.g.
        # 71% rather than 0); fall back to SoM-locked when the EoM is empty
        # but the period started with a position; only 0 when both endpoints
        # are 0. Mirrors the branching in ``_capped_sd_revenue_eom_locked``.
        if inputs.value_eom > 0:
            sd_share = min(entry.cap_usd, inputs.value_eom) / inputs.value_eom
        elif inputs.value_som > 0:
            sd_share = min(entry.cap_usd, inputs.value_som) / inputs.value_som
        else:
            sd_share = Decimal("0")
    else:
        sd_share = _sd_share_at_som(entry, inputs.value_som)
        sd_revenue = actual_revenue * sd_share

    prime_revenue = (actual_revenue - sd_revenue) + inputs.external_revenue

    tw_avg_notional = _time_weighted_notional(
        inputs.venue.notional_principal_usd, period,
    )

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
        external_revenue=inputs.external_revenue,
        tw_avg_value=tw_avg_value,
        cof_excluded=inputs.venue.cof_excluded,
        tw_avg_notional=tw_avg_notional,
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
