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
    # Daily timeseries used by the daily-resolved capped sd_share computation.
    # Expected columns: ``block_date``, ``cum_value`` (capped daily Sky-direct
    # allocation in USD), ``uncapped_value`` (daily total venue value in USD).
    # When provided AND ``sde_entry.kind == "capped"``, the sd_share is
    # computed as ``Σ_d cum_value_d / Σ_d uncapped_value_d`` (value-weighted
    # average daily share — matches Grove's per-day allocation methodology).
    # When ``None``, falls back to the EoM-locked snapshot.
    value_timeseries: "pd.DataFrame | None" = None


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

    **Legacy fallback path** — the primary capped-SDE methodology is now
    daily-resolved (``_capped_sd_revenue_daily_resolved``), which matches
    Grove team's per-day allocation logic in their ``<Asset>_ETH Allocation``
    workbook sheets. This function is retained only for callers that don't
    pass a ``value_timeseries`` (e.g. unit tests that build
    ``VenueRevenueInputs`` directly without the SDE timeseries).

    **History.** PR #101 (2026-06-01) introduced EoM-locked as the primary
    methodology, citing the 2026-05-28 Feb/Mar comparison vs Grove. That
    comparison happened to be on stable-position months where EoM-locked
    and daily-resolved coincide. The 2026-06-04 Jan investigation showed
    Grove uses daily-resolved (effective Sky share for Jan JAAA: 60.6%
    daily vs 71.5% EoM); we reverted to daily-resolved with a burn-day
    override (see ``_capped_sd_revenue_daily_resolved``).

    **Behaviour on a stable position** (constant ``v(t)`` and no inflows):
    this function and the daily-resolved one return identical numbers, so
    using this fallback on a stable-position month is exactly equivalent.

    **Burn day on volatile position.** Sky's capped tranche burned
    mid-period and ``value_eom < cap_usd`` → here ``sd_share = 1.0``
    automatically (cap > value_eom). The daily-resolved path reaches the
    same result via its explicit burn-day override branch (matches Grove's
    JAAA Mar 2026: Sky absorbs essentially the full period net P&L).

    **Full-redemption degenerate case (value_eom = 0).** Falls back to the
    SoM-locked share (``min(cap, value_som) / value_som``) — defensible
    Sky/Prime split rather than silently dropping the entire P&L on Prime.
    Returns 0 only when both endpoints are 0.
    """
    if value_eom > 0:
        sd_share = min(cap_usd, value_eom) / value_eom
        return actual_revenue * sd_share
    if value_som > 0:
        sd_share = min(cap_usd, value_som) / value_som
        return actual_revenue * sd_share
    return Decimal("0")


def _capped_sd_revenue_daily_resolved(
    value_timeseries: pd.DataFrame,
    actual_revenue: Decimal,
    sde_entry: SDEEntry,
    period: Period,
    value_eom: Decimal,
) -> tuple[Decimal, Decimal]:
    """sd_revenue = actual_revenue × Σ_d cum_value_d / Σ_d uncapped_value_d.

    Daily-resolved Sky-direct share: average of the per-day Sky allocation
    fraction, weighted by daily total venue value. Matches Grove's PnL
    workbook per-day allocation methodology (column H × daily NAV in their
    ``<Asset>_ETH Allocation`` sheets, applied to the full period revenue).

    Returns ``(sd_revenue, sd_share)`` so the caller can populate
    ``VenueRevenue.sd_share`` with the same effective fraction.

    **Empirical fit vs Grove (verified Jan 2026 JAAA_ETH).**
    Grove's $1,432,988 ≈ $2,363,115 × (Σ daily sky_alloc / Σ daily value)
                     ≈ $2,363,115 × 60.64%.
    The "% of Grove-Only Portfolio" column G in Grove's sheet is the
    complement (1 - 60.64% ≈ 39.36%) — the value-weighted Grove residual
    fraction across the period.

    **Equivalence with EoM-locked when share is constant.** For a stable
    position with no inflows, daily ``cum_value`` and ``uncapped_value`` are
    constant, so the value-weighted average reduces to the EoM ratio:
    ``cum_value_eom / uncapped_value_eom = min(cap, value_eom) / value_eom``.
    The two methods coincide on stable-position months (e.g. Grove Feb
    2026: JAAA at $454M throughout) and diverge only when the position
    moves materially mid-period.

    **Burn-day override.** When the period contains a ``burn_date`` AND the
    post-burn position has shrunk below the cap (``value_eom < cap_usd``),
    short-circuit to ``sd_share = 1.0`` (Sky bears the full period's
    actual_revenue). Rationale: Grove's workbook treats the burn-month's
    net P&L as essentially Sky's (JAAA Mar 2026: Sky takes -$451,060 of
    -$458,298 total = 98.4%). The daily-Σ method would otherwise
    under-attribute because ``cum_value`` drops to 0 from
    ``usdc_settlement_date`` onward — but Grove's view is that the
    cap-protected slice spanned the bulk of the value-weighted exposure
    and the residual on-chain position is a small Grove-only sliver. The
    ``value_eom < cap_usd`` guard prevents firing if the position is still
    above cap at EoM (i.e. the cap is still constraining), which would
    indicate the position hasn't actually been settled out.

    **Burn-day handling for non-override case.** ``cum_value`` is gated by
    ``_sde_asset_value_timeseries`` (zero pre-start/post-end, ``cap_usd``
    during the in-flight window, ``raw`` when below the cap, etc.), so the
    Σ honours burn semantics natively for periods that don't trigger the
    override (e.g. months entirely before or after the burn date).

    **Degenerate fallback.** If the daily ``uncapped_value`` sum is zero
    (no active days in the period, e.g. fully out-of-window SDE entry),
    return ``(0, 0)`` — neither Sky nor Prime accrues anything from a
    venue with no value to allocate.
    """
    if value_timeseries is None or value_timeseries.empty:
        raise ValueError("daily-resolved sd_share requires non-empty value_timeseries")
    # Burn-day override — see docstring.
    if (
        sde_entry.burn_date is not None
        and period.start <= sde_entry.burn_date <= period.end
        and value_eom < sde_entry.cap_usd
    ):
        return actual_revenue, Decimal("1")
    sum_cum = Decimal("0")
    sum_uncapped = Decimal("0")
    for _, r in value_timeseries.iterrows():
        sum_cum += Decimal(str(r["cum_value"]))
        sum_uncapped += Decimal(str(r["uncapped_value"]))
    if sum_uncapped <= 0:
        return Decimal("0"), Decimal("0")
    sd_share = sum_cum / sum_uncapped
    return actual_revenue * sd_share, sd_share


def compute_venue_revenue(period: Period, inputs: VenueRevenueInputs) -> VenueRevenue:
    """One venue's contribution to prime_agent_revenue under the SDE-split model.

    actual_revenue   = (value_eom − value_som) − period_inflow
                       − (fixed_fee × n_fee_events)    [when fee configured]
    sd_revenue       = actual_revenue × sd_share
    sd_share         = Σ_d cum_value_d / Σ_d uncapped_value_d   (capped SDE,
                                                                 daily-resolved)
                       1.0                                       (burn-day override
                                                                  fires for capped)
                       1                                         (fixed SDE)
                       0                                         (non-SDE)
    prime_revenue    = actual_revenue − sd_revenue + external_revenue

    Capped SDE uses the daily-resolved share when ``value_timeseries`` is
    provided — see ``_capped_sd_revenue_daily_resolved`` for the methodology
    (matches Grove team's per-day allocation), including the burn-day
    override. Falls back to ``_capped_sd_revenue_eom_locked`` when no
    timeseries is plumbed in (legacy / test-only path). Fixed SDE has
    ``sd_share = 1``.

    The ``external_revenue`` stream — off-pool rewards (Merkl, Anchorage,
    etc.) — is added AFTER the SDE split because it doesn't belong to the
    Sky-Direct deal terms (the SDE covers pool-native yield only). For a
    non-SDE venue this is equivalent to ``actual_revenue + external_revenue``.

    The off-chain admin-fee deduction (``fixed_fee × n_fee_events``) is
    applied BEFORE the SDE split — see ``Venue.fixed_fee_per_capital_event_usd``.
    For fixed-SDE venues (BUIDL today) it flows entirely to Sky; for capped
    SDE it splits via the standard sd_share.

    Loss handling: a negative actual_revenue is split the same way — Sky
    absorbs sd_share of the loss, prime absorbs the rest. This matches Grove
    team's PnL workbook (no floor, no shortfall).
    """
    if inputs.actual_revenue_override is not None:
        # Used for sky_savings_token venues where prime revenue is set
        # explicitly (currently 0 — spread reimbursement removed). The
        # fee-detection heuristic below cannot be applied to override
        # venues because they don't consume ``inflow_timeseries`` for
        # actual_revenue. Today no override-path venue has the fee field
        # set — the two are mutually exclusive by configuration.
        assert inputs.venue.fixed_fee_per_capital_event_usd is None, (
            f"venue {inputs.venue.id}: actual_revenue_override and "
            "fixed_fee_per_capital_event_usd are mutually exclusive (the fee "
            "heuristic needs the inflow_timeseries that the override path "
            "doesn't consume). If you need both, plumb them through "
            "separately and add a test for the composition."
        )
        actual_revenue = inputs.actual_revenue_override
        period_inflow = Decimal("0")
        # sky_savings_token venues can have material mid-period inflows; use
        # the daily time-weighted avg over ``inflow_timeseries`` rather than
        # the SoM-only approximation. See PR ``fix/susds-methodology`` /
        # commit 520c278 for the rationale.
        tw_avg_value = _time_weighted_avg_value(
            period, inputs.value_som, inputs.inflow_timeseries,
        )
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

    # Off-chain administrative fee (e.g. BlackRock BUIDL-I $15K per capital
    # operation). The fee is taken at the source by the issuer: a $50M
    # subscription mints $49,985K to the ALM. Detect fee-charged events by
    # the "shaved amount" signature: ``|amount| + fee`` is a round multiple
    # of ``_ROUNDING``. Direction-agnostic (works for both subscriptions and
    # redemptions when fee-charged); a clean $50M mint with no fee gives
    # $50,015K which is NOT a round multiple — correctly skipped.
    # Verified on Grove E10 Jan–Apr 2026: 0/5/1/0 fee events, matching
    # Grove's PnL workbook ($0 / $75K / $15K / $0 fee deduction).
    # Subtracted from ``actual_revenue`` before the SDE split; for fixed-SDE
    # venues (BUIDL is) the fee flows entirely to Sky.
    fee_per_event = inputs.venue.fixed_fee_per_capital_event_usd
    if fee_per_event is not None:
        # Reject zero/None — the fee heuristic depends on the inflow
        # timeseries being pre-filtered to capital-event amounts only.
        # Without an effective ``min_transfer_amount_usd`` filter, the
        # daily yield-distribution mints would be present and could
        # accidentally satisfy the shaved-amount test (e.g. a $985K mint
        # yields $985K + $15K = $1M, a clean multiple). The guard uses
        # ``not …`` so both ``None`` and ``Decimal(0)`` raise.
        if not inputs.venue.min_transfer_amount_usd:
            raise ValueError(
                f"venue {inputs.venue.id}: fixed_fee_per_capital_event_usd "
                "requires min_transfer_amount_usd to be set to a positive "
                "value — set 'min_transfer_amount_usd' on this venue in the "
                "prime YAML config (e.g. 1000000 for BUIDL-style $1M-min "
                "capital events)."
            )
        if inputs.inflow_timeseries is not None and not inputs.inflow_timeseries.empty:
            # ``_ROUNDING`` = the institutional capital-event denomination
            # for venues where this heuristic is used. For BUIDL-I, BlackRock
            # subscribes/redeems in clean $1M multiples (Dune query 7387737
            # bimodal histogram — yield mints <$1M, capital mints ≥ $10M in
            # round $5M/$10M/$25M/$50M chunks). The fee is shaved off the
            # gross at source so the on-chain mint is ``N × $1M − fee`` for
            # fee-charged events. Two invariants tie this constant to the
            # rest of the config:
            #   1. ``_ROUNDING`` must be ≥ the venue's
            #      ``min_transfer_amount_usd`` so the timeseries cannot
            #      contain amounts smaller than _ROUNDING (otherwise a sub-
            #      threshold mint could accidentally satisfy the test).
            #   2. ``_ROUNDING`` matches the issuer's denomination convention.
            #      If BlackRock switched to $500K-multiple subscriptions, this
            #      heuristic would miss fee events on amounts like $499,985
            #      — re-calibrate from the on-chain histogram.
            _ROUNDING = Decimal("1000000")
            ts = inputs.inflow_timeseries
            in_period = ts["block_date"].between(period.start, period.end)
            n_fee_events = 0
            for _, r in ts[in_period].iterrows():
                amount = r["daily_inflow"]
                if amount == 0:
                    continue
                if (abs(amount) + fee_per_event) % _ROUNDING == 0:
                    n_fee_events += 1
            actual_revenue -= fee_per_event * Decimal(n_fee_events)

    entry = inputs.sde_entry
    if entry is not None and entry.kind == "capped":
        # Prefer daily-resolved (Σ cum_value / Σ uncapped_value) when the
        # daily timeseries was plumbed in by the orchestrator — matches
        # Grove's per-day allocation methodology. Fall back to EoM-locked
        # for legacy call paths that don't pass ``value_timeseries`` yet
        # (e.g. tests that build VenueRevenueInputs directly without the
        # SDE timeseries). See ``_capped_sd_revenue_daily_resolved`` for
        # the equivalence proof for stable-position months.
        if inputs.value_timeseries is not None and not inputs.value_timeseries.empty:
            sd_revenue, sd_share = _capped_sd_revenue_daily_resolved(
                inputs.value_timeseries, actual_revenue,
                sde_entry=entry, period=period, value_eom=inputs.value_eom,
            )
        else:
            sd_revenue = _capped_sd_revenue_eom_locked(
                entry.cap_usd, inputs.value_som, inputs.value_eom, actual_revenue,
            )
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
