#!/usr/bin/env python3
"""Decompose a conditional funding-cost gap from saved snapshots, without replay.

This is research evidence, never publication approval or a balancing adjustment.
Unallocated keys name synthetic transaction residues, not destination wallets.
"""
import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

ZERO = D(0)
TOLERANCE = D('0.000001')


def read(path):
    with (gzip.open(path, 'rt') if path.suffix == '.gz' else path.open()) as stream:
        return json.load(stream)


def check(actual, expected, label):
    if abs(actual - expected) > TOLERANCE:
        raise ValueError(f'{label}: {actual} != {expected}')


def decompose(control, snapshot, conditional, daily_accounts, vat):
    """Verify daily stock controls and price disjoint residual components."""
    with localcontext() as context:
        context.prec = 60
        rows = {r['date']: r for r in control['sky_revenue_daily']}
        if len(rows) != len(control['sky_revenue_daily']) or not rows:
            raise ValueError('Empty or duplicate control days')
        days = sorted(rows)
        if (conditional['start'], conditional['end']) != (days[0], days[-1]):
            raise ValueError('Conditional period does not match control')
        start, end = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
        if days != [str(start + timedelta(days=i)) for i in range((end - start).days + 1)]:
            raise ValueError('Missing daily control')
        selected = set(snapshot['identity']['accounts'])
        venues = conditional['allocations']
        if len({v['venue'] for v in venues}) != len(venues):
            raise ValueError('Duplicate venues')
        for venue in venues:
            if set(venue['daily']) != set(days):
                raise ValueError('Incomplete conditional daily principal')
        priced = [v for v in venues if v['conditional_sky_borrowing_cost'] is not None]
        vat_cash = defaultdict(lambda: ZERO)
        for row in vat['daily']:
            vat_cash[row['day']] += D(row['cash_draws_less_repayments'])
        totals = defaultdict(lambda: ZERO)
        global_deductions = defaultdict(lambda: ZERO)
        accounts = {}
        daily = []
        for day in days:
            row = rows[day]
            if D(row['utilized']) <= 0:
                raise ValueError('Positive utilized required for effective rate')
            rate = D(row['daily_sky_rev']) / D(row['utilized'])
            sources = [s for s in snapshot['daily_funding_bridge'][day]['sources'] if s['kind'] == 'sky']
            if len(sources) != 1:
                raise ValueError('Expected one aggregate Sky source')
            sky = sources[0]
            stocks = defaultdict(lambda: ZERO)
            for account, origins in daily_accounts[day].items():
                principal = sum((D(x) for x in origins.values()), ZERO)
                bucket = ('selected' if account in selected else
                          'financing' if account.startswith('financing:') else 'other')
                stocks[bucket] += principal
                if bucket != 'selected':
                    stat = accounts.setdefault(account, {'account': account, 'bucket': bucket,
                        'cost': ZERO, 'average_principal': ZERO})
                    stat['cost'] += principal * rate
                    stat['average_principal'] += principal / len(days)
            for bucket in ('selected', 'financing', 'other'):
                check(stocks[bucket], D(sky[bucket + '_principal']), day + ' ' + bucket)
            check(sum(stocks.values()), D(sky['outstanding_principal']), day + ' complete stock')
            chosen = sum((D(v['daily'][day]['sky_principal']) for v in priced), ZERO)
            all_selected = sum((D(v['daily'][day]['sky_principal']) for v in venues), ZERO)
            check(all_selected, stocks['selected'], day + ' venue coverage')
            if day not in vat_cash:
                raise ValueError('Missing independent cash-debt day')
            parts = {'financing': stocks['financing'] * rate,
                     'other': stocks['other'] * rate,
                     'unpriced_selected': (stocks['selected'] - chosen) * rate,
                     'cash_debt_gap': (vat_cash[day] - D(sky['outstanding_principal'])) * rate,
                     'selected_gross': chosen * rate,
                     'global_deductions': (D(row['cum_debt']) - D(row['utilized'])) * rate}
            for key, value in parts.items():
                totals[key] += value
            for key in ('alm_usds', 'psm_usds', 'sde_av', 'curve_idle', 'lending_idle', 'basin_idle'):
                global_deductions[key] += D(row.get(key, '0')) * rate
            daily.append({'day': day, **parts})
        conditional_sum = sum((D(v['conditional_sky_borrowing_cost']) for v in priced), ZERO)
        deduction_difference = totals['selected_gross'] - conditional_sum - totals['global_deductions']
        components = {k: totals[k] for k in ('financing', 'other', 'unpriced_selected', 'cash_debt_gap')}
        components['deduction_difference'] = deduction_difference
        check(sum(global_deductions.values()), totals['global_deductions'], 'deduction categories')
        applied = {}
        unpriced = {}
        for v in venues:
            gross = sum((D(v['daily'][d]['sky_principal']) * D(rows[d]['daily_sky_rev']) /
                         D(rows[d]['utilized']) for d in days), ZERO)
            if v['conditional_sky_borrowing_cost'] is None:
                unpriced[v['venue']] = gross
            else:
                applied[v['venue']] = gross - D(v['conditional_sky_borrowing_cost'])
        groups = defaultdict(lambda: ZERO)
        for stat in accounts.values():
            groups[stat['account'].split(':', 1)[0]] += stat['cost']
        return {'publishable': False, 'start': days[0], 'end': days[-1],
            'scope': 'Arithmetic decomposition only; no allocation reassignment or publication.',
            'components': components, 'remaining_cost': sum(components.values()),
            'conditional_allocation_cost': conditional_sum,
            'global_deductions': dict(global_deductions), 'applied_deductions': applied,
            'unpriced_selected_costs': unpriced, 'account_groups': dict(groups),
            'accounts': sorted(accounts.values(), key=lambda a: a['cost'], reverse=True), 'daily': daily}


def attach_transactions(report, history_path):
    """Group synthetic residues by observed outflow legs, not guessed recipients."""
    by_identity = {a['account'].removeprefix('unallocated:'): a for a in report['accounts']
                   if a['account'].startswith('unallocated:')}
    matched = set()
    shapes = defaultdict(lambda: ZERO)
    months = defaultdict(lambda: ZERO)
    with localcontext() as context:
        context.prec = 60
        with gzip.open(history_path, 'rt') as stream:
            next(stream)  # Header.
            for line in stream:
                tx = json.loads(line)
                identity = tx['identity']
                if identity not in by_identity:
                    continue
                if identity in matched:
                    raise ValueError('Duplicate transaction identity')
                matched.add(identity)
                stat = by_identity[identity]
                outgoing = [m for m in tx['movements'] if D(m['change']) < 0]
                shape = ' | '.join(sorted(m['account'] for m in outgoing)) or 'no negative movement'
                shapes[shape] += stat['cost']
                months[tx['day'][:7]] += stat['cost']
                stat['transaction'] = {'identity': identity, 'day': tx['day'],
                    'minted': tx['minted'], 'movements': tx['movements']}
    report['unallocated_by_outflow_shape'] = dict(sorted(shapes.items(), key=lambda p: p[1], reverse=True))
    report['unallocated_by_origin_month'] = dict(sorted(months.items()))
    report['unmatched_transaction_keys'] = sorted(set(by_identity) - matched)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--saved-dir', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = {name: args.saved_dir / name for name in ('funding-snapshot.json.gz',
        'conditional-costs.json.gz', 'daily-by-ilk.json.gz', 'vat-accrual.json',
        'funded-history.jsonl.gz', 'global-comparison.json')}
    report = decompose(read(args.control), *(read(paths[n]) for n in (
        'funding-snapshot.json.gz', 'conditional-costs.json.gz', 'daily-by-ilk.json.gz', 'vat-accrual.json')))
    comparison = read(paths['global-comparison.json'])
    check(report['remaining_cost'], D(comparison['remaining_after_msc_and_vat']), 'independent residual')
    check(report['conditional_allocation_cost'], D(comparison['conditional_allocation_sum']), 'conditional sum')
    attach_transactions(report, paths['funded-history.jsonl.gz'])
    report['input_sha256'] = {}
    for path in [args.control, *paths.values()]:
        with path.open('rb') as stream:
            report['input_sha256'][str(path)] = hashlib.file_digest(stream, 'sha256').hexdigest()
    # Keep the review artifact compact; every account remains in the saved input.
    report['account_count'] = len(report['accounts'])
    report['accounts'] = report['accounts'][:30]
    args.output.write_text(json.dumps(report, default=str, indent=2) + '\n')
    print(json.dumps(report['components'], default=str, indent=2))


if __name__ == '__main__':
    main()
