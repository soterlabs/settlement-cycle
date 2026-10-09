"""Read-only monthly costs actually carried by transaction outflow accounts.

Call with a verified local replay's daily_by_ilk snapshots. These are conditional
modeled costs, not certification of funding provenance. Execution evidence can
explain an outflow without proving which lender funded it.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal as D

ZERO = D(0)


def summarize(daily_by_ilk, control, unmatched_outflows, explanation):
    evidence = {r['identity']: r for r in explanation['rows']}
    if len(evidence) != len(explanation['rows']):
        raise ValueError('Duplicate execution explanation')
    days = [date.fromisoformat(r['date']) for r in control['sky_revenue_daily']]
    if not days or len(days) != len(set(days)) or any(d not in daily_by_ilk for d in days):
        raise ValueError('Missing or duplicate daily principal/control')
    days = sorted(days)
    rates = {}
    for row in control['sky_revenue_daily']:
        utilized = D(row['utilized'])
        rate = D(row['daily_sky_rev']) / utilized if utilized else D(str(row['base_apr'])) / 365
        if not rate.is_finite():
            raise ValueError('Invalid daily financing rate')
        rates[date.fromisoformat(row['date'])] = rate
    principal_days, costs = defaultdict(lambda: defaultdict(D)), defaultdict(lambda: defaultdict(D))
    for day in days:
        for account, origins in daily_by_ilk[day].items():
            if not account.startswith('unallocated:'):
                continue
            identity = account.removeprefix('unallocated:')
            if identity not in unmatched_outflows:
                raise ValueError('Funded outflow lacks transaction evidence')
            for ilk, raw in origins.items():
                amount = D(raw)
                if not amount.is_finite():
                    raise ValueError('Invalid daily principal')
                principal_days[identity][ilk] += amount
                costs[identity][ilk] += amount * rates[day]
    totals, grouped, records = defaultdict(D), defaultdict(lambda: defaultdict(D)), []
    for identity, raw in unmatched_outflows.items():
        observed = D(raw)
        if not observed.is_finite() or observed < 0:
            raise ValueError('Invalid historical outflow amount')
        match = evidence.get(identity)
        status = 'no matching execution witness'
        valid_match = match is not None and abs(D(match['observed_outflow']) - observed) <= D('1e-8')
        if valid_match:
            if match['witnessed_components_signed_gain']:
                remainder = abs(D(match['remaining_signed_difference']))
                status = ('execution cost: numeric match' if remainder <= D('1e-8') else
                          'execution cost: sub-cent remainder' if remainder <= D('.01') else
                          'execution cost: material remainder retained')
        for ilk, value in costs[identity].items():
            totals[ilk] += value
            grouped[status][ilk] += value
        closing = daily_by_ilk[days[-1]].get('unallocated:' + identity, {})
        records.append({'identity': identity, 'historical_outflow_value': str(observed),
                        'evidence_status': status,
                        'average_sky_principal_by_ilk': {k: str(v/len(days)) for k, v in principal_days[identity].items()},
                        'closing_sky_principal_by_ilk': {k: str(v) for k, v in closing.items()},
                        'monthly_sky_cost_by_ilk': {k: str(v) for k, v in costs[identity].items()},
                        'execution_remaining_difference': match['remaining_signed_difference'] if valid_match else None})
    return {'scope': __doc__, 'period_start': str(days[0]), 'period_end': str(days[-1]),
            'monthly_sky_cost_by_ilk': {k: str(v) for k, v in totals.items()},
            'cost_by_evidence_status_and_ilk': {k: {i: str(v) for i, v in x.items()} for k, x in grouped.items()},
            'records': records}
