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
    # Daily position value (pre-cap) from ``_sde_asset_value_timeseries``.
    # Columns: ``[block_date, uncapped_value]``. When provided for a
    # ``kind=capped`` SDE venue, ``compute_venue_revenue`` computes a daily
    # sd_share_d = min(cap, v_d)/v_d and accumulates sd_revenue daily instead
    # of locking sd_share at SoM. Ignored for ``kind=fixed`` (sd_share is
    # always 1). None falls back to the SoM-locked behaviour.
    sde_daily_values: pd.DataFrame | None = None
    # For ERC-4626 Centrifuge venues: the exact period inflow derived from
    # on-chain Deposit/Withdraw event ``assets`` amounts (USDC exact).  When
    # set, this value overrides the ``period_inflow`` displayed in the output
    # and the ``actual_revenue`` formula, while ``inflow_timeseries`` (which
    # is the token-transfer-based timeseries, consistent in timing with
    # ``_sde_asset_value_timeseries``) is still passed to
    # ``_daily_capped_sd_revenue`` so the SDE cap-weighting uses a consistent
    # clock.  Without this split the two timeseries would be on different
    # clocks (ERC-20 transfer day vs. Withdraw event day) and the asymmetric
    # cap-weighting would misstate sd_revenue.
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


def _daily_capped_sd_revenue(
    cap_usd: Decimal,
    value_som: Decimal,
    sde_daily_values: pd.DataFrame,
    inflow_timeseries: pd.DataFrame,
) -> Decimal:
    """Accumulate sd_revenue using per-day position snapshots.

    For each day ``d`` in the period:

        sd_share_d  = min(cap_usd, v_d) / v_d    (0 if v_d == 0)
        daily_rev_d = (v_d − v_{d−1}) − inflow_d
        sd_rev_d    = daily_rev_d × sd_share_d

    ``v_0`` (the "previous value" for day 1) is ``value_som`` from RPC so
    that ``Σ daily_rev_d`` telescopes to ``actual_revenue``.

    ``inflow_d`` is looked up from ``inflow_timeseries.daily_inflow`` by date;
    days with no row contribute zero inflow.
    """
    inflow_by_date: dict = {}
    if inflow_timeseries is not None and not inflow_timeseries.empty:
        for _, row in inflow_timeseries.iterrows():
            v = row["daily_inflow"]
            inflow_by_date[row["block_date"]] = (
                v if isinstance(v, Decimal) else Decimal(str(v))
            )

    total = Decimal("0")
    prev = value_som
    for _, row in sde_daily_values.sort_values("block_date").iterrows():
        v_raw = row["uncapped_value"]
        v = v_raw if isinstance(v_raw, Decimal) else Decimal(str(v_raw))
        inflow_d = inflow_by_date.get(row["block_date"], Decimal("0"))
        daily_rev = (v - prev) - inflow_d
        if v > 0:
            sd_share_d = min(cap_usd, v) / v
            total += daily_rev * sd_share_d
        # v == 0 → sd_share_d = 0 → daily_rev contributes 0 to Sky
        prev = v
    return total


def compute_venue_revenue(period: Period, inputs: VenueRevenueInputs) -> VenueRevenue:
    """One venue's contribution to prime_agent_revenue under the SDE-split model.

    actual_revenue   = (value_eom − value_som) − period_inflow
    sd_revenue       = Σ_d daily_rev_d × sd_share_d      (daily for capped SDE)
                       or actual_revenue × sd_share_som   (fallback / fixed)
    sd_share         = sd_revenue / actual_revenue        (effective average, display only)
    prime_revenue    = actual_revenue − sd_revenue + external_revenue

    For ``kind=capped`` SDE venues with ``sde_daily_values`` supplied: each
    day's ``sd_share_d = min(cap, v_d) / v_d`` is applied to that day's
    revenue increment, so capital flows mid-month are reflected in the split
    rather than being locked at SoM. See ``_daily_capped_sd_revenue``.

    For ``kind=fixed`` or when no ``sde_daily_values`` is provided: falls back
    to locking ``sd_share`` at SoM via ``_sd_share_at_som``.

    The ``external_revenue`` stream — off-pool rewards (Merkl, Anchorage,
    etc.) — is added AFTER the SDE split because it doesn't belong to the
    Sky-Direct deal terms (the SDE covers pool-native yield only). For a
    non-SDE venue this is equivalent to ``actual_revenue + external_revenue``.

    Loss handling: a negative actual_revenue is split the same way — Sky
    absorbs sd_share of the loss, prime absorbs the rest. This matches Grove
    team's PnL workbook (no floor, no shortfall).
    """
    _rwa_actual_revenue: Decimal | None = None  # set only in erc4626_period_inflow branch
    if inputs.actual_revenue_override is not None:
        actual_revenue = inputs.actual_revenue_override
        period_inflow = Decimal("0")
        # Override venues (sUSDS spread at ALM) have a stable position
        # roughly at value_som; the simple value_som is the best avg
        # available without a daily series.
        tw_avg_value = inputs.value_som
    elif inputs.erc4626_period_inflow is not None:
        # ERC-4626 Centrifuge venues: use exact vault-event USDC amounts for
        # the period inflow and revenue formula; inflow_timeseries (token-
        # transfer based, same clock as _sde_ts) is still used below by
        # _daily_capped_sd_revenue so the daily cap-weighting is consistent.
        period_inflow = inputs.erc4626_period_inflow
        actual_revenue = (inputs.value_eom - inputs.value_som) - period_inflow
        inflow_df = inputs.inflow_timeseries
        tw_avg_value = _time_weighted_avg_value(
            period, inputs.value_som, inflow_df,
        )
        # Also compute the token-transfer actual_revenue (denominator for
        # sd_share scaling below).  This is needed so the effective sd_share
        # from _daily_capped_sd_revenue (which ran against the rwa inflow_ts)
        # can be re-applied to the vault-event actual_revenue.
        _rwa_cum_som = cum_at_or_before(
            inflow_df, "cum_inflow", period.start - timedelta(days=1),
        )
        _rwa_cum_eom = cum_at_or_before(inflow_df, "cum_inflow", period.end)
        _rwa_actual_revenue = (
            (inputs.value_eom - inputs.value_som) - (_rwa_cum_eom - _rwa_cum_som)
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
    if (
        entry is not None
        and entry.kind == "capped"
        and inputs.sde_daily_values is not None
        and not inputs.sde_daily_values.empty
    ):
        sd_revenue = _daily_capped_sd_revenue(
            entry.cap_usd,
            inputs.value_som,
            inputs.sde_daily_values,
            inputs.inflow_timeseries,
        )
        # For ERC-4626 Centrifuge venues: _daily_capped_sd_revenue ran against
        # the token-transfer inflow_ts (consistent clock with _sde_ts), but
        # actual_revenue uses vault-event flows.  The two methodologies can
        # differ because vault-event ``assets`` is exact USDC while the token-
        # transfer path reprices net share movements at the end-of-day NAV.
        # For Centrifuge ERC-7540 the transfer (requestRedeem) and the USDC
        # receipt (claimRedeem) happen in different transactions: the delta
        # represents intra-epoch yield that accrued on the redeemed shares inside
        # the vault, never captured by the NAV-repricing path.
        #
        # Split this delta using the SOM sd_share (= min(cap, SOM) / SOM).
        # The redeemed shares were part of the pre-redemption position; their
        # Sky/Prime attribution is determined by where they sat relative to the
        # cap at that time.  SOM is the best available proxy for the
        # pre-redemption position when a single large redemption dominates the
        # period (as is typical for Cat E venues).  Using the period-average
        # sd_share would over-weight post-redemption days where the lower
        # remaining position inflates sd_share and over-attributes to Sky.
        #
        # Known limitation: if multiple redemptions occurred at different times,
        # or if the position changed significantly before the requestRedeem, the
        # SOM sd_share is stale.  Track requestRedeem Transfer dates for a full
        # fix (see PR description).
        if _rwa_actual_revenue is not None:
            _delta = actual_revenue - _rwa_actual_revenue
            _som_sd_share = (
                min(entry.cap_usd, inputs.value_som) / inputs.value_som
                if inputs.value_som > 0 else Decimal("0")
            )
            sd_revenue = sd_revenue + _delta * _som_sd_share
        # Effective (average) sd_share for display — sd_revenue / actual_revenue.
        sd_share = (
            sd_revenue / actual_revenue if actual_revenue != 0 else Decimal("0")
        )
    else:
        sd_share = _sd_share_at_som(entry, inputs.value_som)
        sd_revenue = actual_revenue * sd_share

    prime_revenue = (actual_revenue - sd_revenue) + inputs.external_revenue

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
