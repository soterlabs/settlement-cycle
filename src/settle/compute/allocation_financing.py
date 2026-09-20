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


def allocation_financing(pnl, history) -> dict:
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
        if any(a in replay.uncertain_accounts for a in accounts):
            reason = "Unmatched funding receipts in account history"
        principal = {d: sum((replay.daily[d].get(a, ZERO) for a in accounts), ZERO) for d in days}
        average_principal = sum(principal.values(), ZERO) / Decimal(n_days)
        average_value = max(venue.tw_avg_value, venue.tw_avg_notional)
        idle = venue.lending_idle_tw_avg_usd + venue.amm_idle_usds_tw_avg_usd
        # Existing idle values are period means. Their fraction is applied to
        # borrowed basis, never treated as an additional source of borrowing.
        idle_fraction = min(Decimal(1), idle / average_value) if average_value > ZERO else ZERO
        cost = ZERO
        for day in days:
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
    return {
        "method": "transaction_weighted_average_borrowed_basis_v1",
        "allocations": rows,
        "allocation_cost_of_funds": total_cost,
        "prime_financing_adjustment": existing_cost - total_cost,
        "existing_cost_of_funds": existing_cost,
        "sde_revenue": pnl.sde_revenue,
        "spread_reimbursement": pnl.susds_spread_reimbursement,
        "existing_sky_revenue": pnl.sky_revenue,
        "unmatched_receipts": replay.unmatched_receipts,
        "unmatched_outflows": replay.unmatched_outflows,
        "realised_principal_loss": replay.ledger.realised_principal_loss,
    }
