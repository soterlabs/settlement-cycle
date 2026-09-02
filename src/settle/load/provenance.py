"""Provenance JSON — the audit trail for a settlement run.

Records: pin blocks, source identifiers, generation timestamp, pipeline version.
Does NOT record raw query results — those live in the Extract cache.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from ..domain.monthly_pnl import MonthlyPnL


def render_provenance(
    pnl: MonthlyPnL,
    *,
    sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the provenance dict. Pure — easy to test/snapshot."""
    return {
        "settle_version": __version__,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prime_id": pnl.prime_id,
        "month": str(pnl.month),
        "period": {
            "start": pnl.period.start.isoformat(),
            "end": pnl.period.end.isoformat(),
            "n_days": pnl.period.n_days,
        },
        "pin_blocks_eom": {c.value: blk for c, blk in pnl.period.pin_blocks.items()},
        "pin_blocks_som": {c.value: blk for c, blk in pnl.pin_blocks_som.items()},
        "results": {
            "sky_revenue": str(pnl.sky_revenue),
            "sky_direct_shortfall": str(pnl.sky_direct_shortfall),
            "agent_rate": str(pnl.agent_rate),
            "prime_agent_revenue": str(pnl.prime_agent_revenue),
            "distribution_rewards": str(pnl.distribution_rewards),
            # Chronicle Points (Grove only today) — Demand-Side component,
            # summed into prime_agent_total_revenue. Written for every
            # prime ($0 when the program doesn't apply); the summary
            # renderer shows the row only when non-zero.
            "chronicle_points": str(pnl.chronicle_points),
            # Governance Accessibility Rewards (Skybase only today) —
            # Demand-Side component: GarConfig.share (1%) × the month's
            # consolidated Sky Net Revenue, paid at the MSC executing the
            # following month. Same render convention as chronicle_points:
            # written for every prime; row shown only when non-zero.
            "gar": str(pnl.gar),
            # Audit trail: share × SNR derivation (incl. the sky_total
            # artifact's generation timestamp), or "".
            "gar_basis": pnl.gar_basis,
            "prime_agent_total_revenue": str(pnl.prime_agent_total_revenue),
            # 30 bps Prime Revenue components computed outside the venue loop
            # (PRD §17.11). Surfaced here so downstream reporting can
            # reconcile Σ venue.revenue with prime_agent_revenue. Both are
            # zero for primes without sUSDS-in-Curve or PSM3 positions.
            "curve_susds_spread": str(pnl.curve_susds_spread),
            "psm3_susds_spread": str(pnl.psm3_susds_spread),
            # Case 3a (PRD §10): SSR appreciation on the PSM3 sUSDS slice,
            # booked into prime_agent_revenue as a prime-level addition
            # (no venue row). Σ venue.revenue + this = prime_agent_revenue.
            "psm3_susds_appreciation": str(pnl.psm3_susds_appreciation),
            # Total 30 bps spread deducted from sky_revenue for sky_savings_token
            # Cat B venues. sky_revenue is already net of this deduction.
            # Used by build_monthly_report.py to recover gross-BR cof_total.
            "susds_spread_reimbursement": str(pnl.susds_spread_reimbursement),
            # Sky Revenue that would result if no deductions were subtracted
            # from utilized (i.e., utilized = cum_debt each day). Same BR /
            # subsidy schedule as actual sky_revenue. Display-only — not
            # part of any settlement invariant.
            "sky_revenue_gross": str(pnl.sky_revenue_gross),
            # SDE revenue (Σ venue.sd_revenue across the breakdown). Already
            # rolled into sky_revenue; surfaced separately for the summary
            # headline.
            "sde_revenue": str(pnl.sde_revenue),
            # Net monthly P&L: prime_agent_revenue + agent_rate +
            # distribution_rewards − sky_revenue. Negative when sky_revenue
            # exceeds total prime revenue.
            "monthly_pnl": str(pnl.monthly_pnl),
        },
        "venue_breakdown": [
            {
                "venue_id": v.venue_id,
                "label": v.label,
                "value_som": str(v.value_som),
                "value_eom": str(v.value_eom),
                "period_inflow": str(v.period_inflow),
                "revenue": str(v.revenue),
                # Sky Direct fields — zero for non-Sky-Direct venues. Captured
                # so an auditor can reconstruct sky_revenue from the breakdown:
                #   sky_revenue = utilized × BR − Σ sky_direct_shortfall
                # and prime's per-venue revenue: max(0, actual_revenue − br_charge).
                "actual_revenue": str(v.actual_revenue),
                # Off-pool rewards (Merkl drops, Anchorage sweeps, …) — flows
                # 100% to prime, NOT subject to SDE-splitting. Already rolled
                # into ``revenue`` above; surfaced separately so an auditor can
                # see the breakdown between closed-form yield and external
                # rewards. See ``normalize.positions._atoken_external_revenue_usd``.
                "external_revenue": str(v.external_revenue),
                # Sky-direct slice of actual_revenue. For capped SDE venues
                # this is ``actual_revenue × min(cap_usd, value_eom) / value_eom``
                # (EoM-locked — see ``_capped_sd_revenue_eom_locked``). Falls
                # back to SoM-locked share when value_eom = 0. For fixed SDE
                # it equals actual_revenue. Can be negative when a capped SDE
                # position takes a loss (e.g., the JAAA E8 Mar 2026 tranche
                # burn). Reporting layers display the value as-is without
                # clamping to zero.
                "sd_revenue": str(v.sd_revenue),
                # Theoretical sd_share for the period — for capped SDE this
                # is ``min(cap_usd, value_eom) / value_eom`` (or SoM-locked
                # fallback). For fixed SDE always 1. Display-only; recomputed
                # from cap_usd + value_eom + value_som rather than derived
                # from sd_revenue / actual_revenue (so a break-even period
                # still reports a meaningful share rather than 0).
                "sd_share": str(v.sd_share),
                # Time-weighted average daily principal across the period
                # (mean of value_som + cum_inflow_d). Surfaced for post-hoc
                # CoF allocation in reporting sheets.
                "tw_avg_value_usd": str(v.tw_avg_value),
                # Time-weighted average of the off-chain notional principal
                # (from ``Venue.notional_principal_usd``). Non-zero only for
                # cash-distribution-only venues (E21 Galaxy CLO, etc.).
                # The CoF allocator uses max(tw_avg_value, tw_avg_notional)
                # as the effective avg.
                "tw_avg_notional_usd": str(v.tw_avg_notional),
                # 30 bps spread deducted from sky_revenue for this venue.
                # Non-zero only for sky_savings_token Cat B venues.
                "susds_spread_reimbursement": str(v.susds_spread_reimbursement),
                # When True, this venue's avg_value is excluded from the CoF
                # allocation denominator in post-hoc reporting. Mirrors the
                # ``Venue.cof_excluded`` flag, propagated here so the
                # settlement xlsx builder doesn't have to re-load YAML.
                "cof_excluded": v.cof_excluded,
                # ``Venue.pricing_category.value`` ("A", "B", …, "S2").
                # Lets reporting branch on venue kind (e.g. the grove-sheet
                # Savings-V2 ``deduction_avg`` path) without re-loading
                # YAML or inferring the kind from the sign of
                # ``actual_revenue``.
                "pricing_category": v.pricing_category,
                # When True, the renderer suppresses per-venue PnL columns
                # in the summary.md / xlsx Venues table; only the display
                # row is hidden. Currently set on Spark Savings V2 vaults
                # (S56/S57/S59/S60), which are position-only (revenue $0,
                # excluded from ``prime_agent_revenue``) — showing a $0
                # PnL row per vault would be noise next to the real
                # position values.
                "hide_per_venue_pnl": v.hide_per_venue_pnl,
                # Time-weighted average daily lending-idle deduction for
                # Cat C/D venues with ``lending_idle_usds: true``. The
                # post-hoc CoF allocator subtracts this from avg_value
                # before splitting cof_total across venues.
                "lending_idle_tw_avg_usd": str(v.lending_idle_tw_avg_usd),
                "amm_idle_usds_tw_avg_usd": str(v.amm_idle_usds_tw_avg_usd),
                "br_charge": str(v.br_charge),
                "sky_direct_shortfall": str(v.sky_direct_shortfall),
            }
            for v in pnl.venue_breakdown
        ],
        # Display-only venues — tracked for reports, excluded from MSC
        # totals. Empty list for primes with no off-protocol positions.
        # See ``Venue.display_only``.
        "display_only_breakdown": [
            {
                "venue_id": v.venue_id,
                "label": v.label,
                "value_som": str(v.value_som),
                "value_eom": str(v.value_eom),
            }
            for v in pnl.display_only_breakdown
        ],
        # Distribution Rewards per ref code for the period (settle-dr-dune
        # Summary tab). Sums to ``results.distribution_rewards``. Empty for
        # primes without tagged DR. Rendered as the summary.md "DR per ref
        # code" table.
        "dr_breakdown": [
            {
                "ref_code": d.get("ref_code", ""),
                "amount": str(d.get("amount", "0")),
                "notes": d.get("notes", ""),
            }
            for d in pnl.dr_breakdown
        ],
        # Day-by-day Sky-revenue breakdown: cum_debt (= Σ Vat dart from
        # frob+grab, then scaled by Vat.ilks[ilk].rate_d / 1e27 at that
        # day's EoD block — i.e. actual outstanding USDS, not raw
        # normalised Art), the five deductions that derive ``utilized``,
        # the SSR / base / subsidised APYs effective each day, the
        # subsidy ramp index, and the daily Sky charge (actual +
        # gross-on-cum_debt). Empty list for runs where the daily series
        # wasn't captured. Consumed by the xlsx "Debt" tab for prime-team
        # reconciliation.
        "sky_revenue_daily": pnl.sky_revenue_daily,
        # Per-period subsidised-borrowing aggregates (None for non-subsidy
        # primes). Single source for the xlsx "Rates & subsidy" panel —
        # effective rate, $1B tranche split, and the subsidy's $ benefit.
        "subsidy_summary": pnl.subsidy_summary,
        # Per-venue daily SDE asset-value series. Used by the xlsx "SDE daily"
        # tab to render the Sky / Grove / in-flight decomposition (phase-
        # based: pre-burn / in-flight / settled) without re-running on-chain
        # reads. Empty list for primes / months without active SDE venues.
        "sde_daily_breakdown": [
            {
                "venue_id": b.venue_id,
                "label": b.label,
                "cap_usd": str(b.cap_usd) if b.cap_usd is not None else None,
                "burn_date": b.burn_date.isoformat() if b.burn_date else None,
                "usdc_settlement_date": (
                    b.usdc_settlement_date.isoformat()
                    if b.usdc_settlement_date else None
                ),
                "end_date": b.end_date.isoformat() if b.end_date else None,
                "daily": [
                    {
                        "block_date": r["block_date"].isoformat(),
                        "cum_value": str(r["cum_value"]),
                        "uncapped_value": str(r["uncapped_value"]),
                    }
                    for r in b.daily
                ],
            }
            for b in pnl.sde_daily_breakdown
        ],
        "sources": sources or {},
    }


def write_provenance(
    pnl: MonthlyPnL,
    dest: Path,
    *,
    sources: dict[str, str] | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = render_provenance(pnl, sources=sources)
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return dest
