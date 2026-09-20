"""Allocation analytics layered over an unchanged settlement result.

No result here is an input to Sky revenue, ilk debt, or settlement. The rate is
the existing daily blended borrowing rate, including its subsidy allocation.
The separate financing adjustment reconciles to existing BR cost; SDE revenue
and spread reimbursements remain separate in the bridge to Sky's full claim.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, localcontext

from .allocation_capital import ZERO, replay_history


def annualized_yield(revenue: Decimal, average_value: Decimal, days: int) -> Decimal | None:
    """Effective annual yield; undefined for zero exposure or total-loss returns."""
    if average_value <= ZERO or days <= 0:
        return None
    period_factor = Decimal(1) + revenue / average_value
    if period_factor <= ZERO:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        return period_factor ** (Decimal(365) / Decimal(days)) - Decimal(1)


def unavailable_financing(pnl, error: Exception) -> dict:
    """Keep known gross yields when funding extraction is unavailable.

    Analytics never substitutes an estimated net yield or blocks publication
    of the already-computed settlement. Error metadata is for provenance only.
    """
    days = (pnl.period.end - pnl.period.start).days + 1
    rows = [{
        'venue_id': v.venue_id, 'basis_status': 'unresolved',
        'basis_detail': 'Funding history unavailable',
        'borrowed_principal_som': None, 'borrowed_principal_eom': None,
        'borrowed_principal_average': None, 'cost_of_funds': None, 'net_pnl': None,
        'gross_apy': annualized_yield(v.actual_revenue + v.external_revenue,
                                     max(v.tw_avg_value, v.tw_avg_notional), days),
        'net_apy': None,
    } for v in pnl.venue_breakdown]
    return {'method': 'transaction_weighted_average_borrowed_basis_v1',
            'status': 'unavailable', 'error_type': type(error).__name__, 'allocations': rows,
            'reconciliation': {'complete': False, 'allocation_sum': None,
                'existing_cost_of_funds': pnl.sky_revenue - pnl.sde_revenue + pnl.susds_spread_reimbursement,
                'difference': None, 'within_one_cent': None,
                'unresolved_allocations': [r['venue_id'] for r in rows]}}


def allocation_financing(pnl, history, *, idle_fractions=None) -> dict:
    start, end = pnl.period.start, pnl.period.end
    replay = replay_history(history, start - timedelta(days=1), end)
    n_days = (end - start).days + 1
    days = [start + timedelta(days=i) for i in range(n_days)]
    rates = {}
    for row in pnl.sky_revenue_daily:
        day = date.fromisoformat(row["date"])
        utilized = Decimal(str(row["utilized"]))
        charge = Decimal(str(row["daily_sky_rev"]))
        # If global utilized is zero, independent venue charges still use BR;
        # the financing adjustment captures the global deductions.
        rates[day] = (charge / utilized if utilized > ZERO else
                      Decimal(str(row["base_apr"])) / Decimal(365))
    if set(days) - rates.keys():
        raise ValueError("Allocation financing requires every existing daily borrowing rate")
    sde_daily = {b.venue_id: {r["block_date"]: r for r in b.daily}
                 for b in pnl.sde_daily_breakdown}
    rows = []
    total_cost = ZERO
    for venue in pnl.venue_breakdown:
        account = history.venue_accounts.get(venue.venue_id)
        accounts = ([account] if account is not None else []) + history.custody_accounts.get(venue.venue_id, [])
        reason = history.unsupported.get(venue.venue_id)
        if account is None and reason is None:
            reason = "No capital account mapping"
        if any(a in replay.uncertain_daily[d] for a in accounts for d in days):
            reason = "Unmatched funding receipts in account history"
        principal = {d: sum((replay.daily[d].get(a, ZERO) for a in accounts), ZERO) for d in days}
        average_principal = sum(principal.values(), ZERO) / Decimal(n_days)
        average_value = max(venue.tw_avg_value, venue.tw_avg_notional)
        idle = venue.lending_idle_tw_avg_usd + venue.amm_idle_usds_tw_avg_usd
        daily_idle = (idle_fractions or {}).get(venue.venue_id, {})
        if idle > ZERO and set(days) - daily_idle.keys():
            reason = reason or "Daily idle funding fractions are incomplete"
        cost = ZERO
        for day in days:
            idle_fraction = daily_idle.get(day, ZERO)
            if not idle_fraction.is_finite() or not ZERO <= idle_fraction <= Decimal(1):
                raise ValueError(f"Invalid daily idle fraction for {venue.venue_id} on {day}")
            sde_fraction = venue.sd_share
            sde = sde_daily.get(venue.venue_id, {}).get(day)
            if sde and sde["uncapped_value"] > ZERO:
                sde_fraction = sde["cum_value"] / sde["uncapped_value"]
            fraction = max(ZERO, Decimal(1) - sde_fraction - idle_fraction)
            if not venue.cof_excluded and account not in history.idle_accounts:
                cost += principal[day] * fraction * rates[day]
        if reason:
            # Missing provenance is not a $0 funding charge. Retain null until
            # the custody/payment adapter can account for that venue.
            cost_out = None
            net_pnl = None
        else:
            cost_out = cost
            net_pnl = venue.revenue - cost
            total_cost += cost
        rows.append({
            "venue_id": venue.venue_id,
            "basis_status": "unresolved" if reason else "traced",
            "basis_detail": reason,
            "borrowed_principal_som": sum((replay.daily[start - timedelta(days=1)].get(a, ZERO) for a in accounts), ZERO),
            "borrowed_principal_eom": principal[end],
            "borrowed_principal_average": average_principal,
            "cost_of_funds": cost_out,
            "net_pnl": net_pnl,
            "gross_apy": annualized_yield(venue.actual_revenue + venue.external_revenue,
                                           average_value, n_days),
            "net_apy": annualized_yield(net_pnl, average_value, n_days) if net_pnl is not None else None,
        })
    existing_cost = pnl.sky_revenue - pnl.sde_revenue + pnl.susds_spread_reimbursement
    unresolved = [r['venue_id'] for r in rows if r['cost_of_funds'] is None]
    difference = total_cost - existing_cost
    return {
        "method": "transaction_weighted_average_borrowed_basis_v1",
        "status": "partial" if unresolved else "calculated",
        "allocations": rows,
        "allocation_cost_of_funds": total_cost,
        "prime_financing_adjustment": existing_cost - total_cost,
        "existing_cost_of_funds": existing_cost,
        "sde_revenue": pnl.sde_revenue,
        "spread_reimbursement": pnl.susds_spread_reimbursement,
        "existing_sky_revenue": pnl.sky_revenue,
        "reconciliation": {
            "allocation_sum": total_cost,
            "existing_cost_of_funds": existing_cost,
            "difference": difference,
            "complete": not unresolved and not replay.unmatched_receipts and not replay.unmatched_outflows,
            "unresolved_allocations": unresolved,
            "within_one_cent": abs(difference) <= Decimal('0.01'),
        },
        "unmatched_receipts": replay.unmatched_receipts,
        "unmatched_outflows": replay.unmatched_outflows,
        "realised_principal_loss": replay.ledger.realised_principal_loss,
    }
