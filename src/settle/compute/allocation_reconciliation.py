"""Read-only per-ilk reconciliation to pinned debt and settlement controls.

The reference charge uses the settlement's existing blended daily rate. It is
not a new subsidy calculation and never changes the payable borrowing charge.
Deduction ownership must be supplied explicitly for multi-ilk primes.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal as D

ZERO = D(0)
FIELDS = ('alm_usds', 'psm_usds', 'sde_av', 'curve_idle', 'lending_idle', 'basin_idle')


def validate_idle_control(idle, control):
    """Reject stale/incomplete idle dollars within the published venue scope.

    New venues on main must not retroactively add exemptions to a historical
    control. Report exclusions explicitly; never scale inputs to fit totals.
    """
    included = ({r['venue_id'] for r in control['venue_breakdown']}
                if 'venue_breakdown' in control else set(idle))
    for row in control['sky_revenue_daily']:
        day = date.fromisoformat(row['date'])
        actual = sum((values.get(day, ZERO) for vid, values in idle.items() if vid in included), ZERO)
        expected = D(str(row.get('curve_idle', 0))) + D(str(row.get('lending_idle', 0)))
        if not actual.is_finite() or abs(actual - expected) > D('.01'):
            raise ValueError(f'Daily idle deductions do not reproduce settlement control on {day}')
    return sorted(set(idle) - included)


def reconcile_ilks(finance, control, debt_days, deduction_owners):
    days = {r['day']: r for r in debt_days}
    control_days = {r['date'] for r in control['sky_revenue_daily']}
    if len(days) != len(debt_days) or set(days) != control_days:
        raise ValueError('Debt control must contain exactly one entry per settlement day')
    totals = defaultdict(lambda: {'global_cost': ZERO, 'msc_cost': ZERO, 'allocation_cost': ZERO})
    daily = []
    for row in control['sky_revenue_daily']:
        day = row['date']
        by_ilk = {'0x' + k.removeprefix('0x'): v for k, v in days[day]['by_ilk'].items()}
        attributed = finance.get('daily_allocation_by_ilk', {}).get(day, {})
        if any(D(str(v['cost'])) for k, v in attributed.items() if k not in by_ilk):
            raise ValueError('Allocation costs have an unknown funding ilk')
        debt = sum(D(v['debt']) for v in by_ilk.values())
        if abs(debt - D(row['cum_debt'])) > D('.01'):
            raise ValueError('Per-ilk debt does not reproduce pinned settlement debt')
        deductions = defaultdict(D)
        for field in FIELDS:
            amount = D(str(row.get(field, 0)))
            if amount:
                owner = deduction_owners.get(field)
                if owner not in by_ilk:
                    raise ValueError(f'Missing verified deduction owner: {field}')
                deductions[owner] += amount
        utilized = D(row['utilized'])
        if abs(debt - sum(deductions.values()) - utilized) > D('.01'):
            raise ValueError('Daily dollar deductions do not reproduce settlement utilized')
        rate = D(row['daily_sky_rev']) / utilized if utilized > 0 else D(str(row['base_apr'])) / 365
        day_cost = ZERO
        for ilk, values in by_ilk.items():
            principal = D(values['debt'])
            used = principal - deductions[ilk]
            if used < 0:
                raise ValueError('Negative per-ilk utilization requires explicit cross-ilk treatment')
            msc = D(values['prior_msc_debt']) + D(values['current_month_msc_debt'])
            cost = used * rate
            msc_cost = min(msc, used) * rate
            allocation = D(str(attributed.get(ilk, {}).get('cost', 0)))
            totals[ilk]['global_cost'] += cost
            totals[ilk]['msc_cost'] += msc_cost
            totals[ilk]['allocation_cost'] += allocation
            day_cost += cost
            daily.append(dict(day=day, ilk=ilk, debt=principal, deductions=deductions[ilk],
                              utilized=used, global_cost=cost, msc_cost=msc_cost,
                              allocation_cost=allocation, difference=allocation - (cost - msc_cost)))
        if abs(day_cost - D(row['daily_sky_rev'])) > D('.01'):
            raise ValueError('Per-ilk charges do not reproduce existing global borrowing cost')
    summaries = {}
    global_sum = sum((v['global_cost'] for v in totals.values()), ZERO)
    if abs(global_sum - D(str(finance['reconciliation']['existing_cost_of_funds']))) > D('.01'):
        raise ValueError('Per-ilk charges do not reproduce the period borrowing cost')
    allocation_sum = sum((v['allocation_cost'] for v in totals.values()), ZERO)
    if abs(allocation_sum - D(str(finance['reconciliation']['allocation_sum']))) > D('.01'):
        raise ValueError('Per-ilk allocation costs do not reproduce the allocation sum')
    for ilk, values in totals.items():
        target = values['global_cost'] - values['msc_cost']
        difference = values['allocation_cost'] - target
        summaries[ilk] = {**values, 'global_excluding_msc': target, 'difference': difference,
                          'within_one_cent': abs(difference) <= D('.01'),
                          'complete': (finance['reconciliation']['source_complete']
                                       and abs(difference) <= D('.01')),
                          'source_complete': finance['reconciliation']['source_complete']}
    return {'method': 'existing_daily_blended_rate_with_explicit_deduction_owners',
            'by_ilk': summaries, 'daily': daily, 'deduction_owners': deduction_owners}
