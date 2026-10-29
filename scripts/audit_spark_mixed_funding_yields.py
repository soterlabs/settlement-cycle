#!/usr/bin/env python3
"""Conditional daily Spark funding splits; never a monthly API publication input.

Reuse the retained Savings/refinancing ledger. Output makes its assumptions,
unknown funding and independent capital-basis residuals visible. A conditional
cost or net APY is not a certified Sky-funded point estimate.
"""
import argparse
import gzip
import hashlib
import json
import logging
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

from report_allocation_capital_readiness import price_daily
from report_allocation_yields import build as build_yields

from settle.compute.allocation_capital import replay_history
from settle.compute.allocation_financing import annualized_yield
from settle.compute.allocation_reconciliation import validate_idle_control
from settle.normalize.allocation_history_cache import load_history

ZERO = D(0)


def summarize(replay, history, readiness, control, idle, venues):
    start, end = date.fromisoformat(readiness['start']), date.fromisoformat(readiness['end'])
    if (readiness['prime'] != 'spark' or control['prime_id'] != 'spark'
            or control['period']['start'] != str(start) or control['period']['end'] != str(end)):
        raise ValueError('Spark funding, revenue and capital periods must match')
    days = [str(start + timedelta(days=i)) for i in range((end-start).days+1)]
    rows = control['sky_revenue_daily']
    if len(rows) != len(days) or {r['date'] for r in rows} != set(days):
        raise ValueError('Incomplete daily financing rates')
    rates = {r['date']: D(str(r['daily_sky_rev'])) / D(str(r['utilized']))
             if D(str(r['utilized'])) > 0 else D(str(r['base_apr'])) / 365 for r in rows}
    validate_idle_control({v: {date.fromisoformat(d): D(x) for d, x in values.items()}
                           for v, values in idle['daily'].items()}, control)
    sde = {r['venue_id']: {d['block_date']: d['cum_value'] for d in r['daily']}
           for r in control.get('sde_daily_breakdown', [])}
    revenues = {r['venue_id']: r for r in control['venue_breakdown']}
    capitals = {r['venue']: r for r in readiness['allocations']}
    measured_yields = {r['venue']: r for r in build_yields({'results': [readiness]}, {'spark': control})['results'][0]['allocations']}
    output = []
    for venue in venues:
        if venue in history.covered_by_boundary:
            raise ValueError('Custody covered by another venue cannot be charged again')
        accounts = ([history.venue_accounts[venue]] if venue in history.venue_accounts else []) + history.custody_accounts.get(venue, [])
        if not accounts or venue not in capitals or venue not in revenues:
            raise ValueError('Missing selected venue capital or revenue')
        daily = {}
        for d in days:
            observed = replay.daily_funding[date.fromisoformat(d)]
            parts = [observed[a] for a in accounts]
            sky = sum((p['sky_principal'] for p in parts), ZERO)
            savings = sum((sum(p['savings_by_source'].values(), ZERO) for p in parts), ZERO)
            original = D(capitals[venue]['daily_principal'][d])
            if any(not x.is_finite() or x < 0 for x in (sky, savings, original)):
                raise ValueError('Invalid funding component')
            daily[d] = {'sky_principal': sky, 'savings_principal': savings,
                        'original_capital_control': original,
                        'capital_basis_difference': original-sky-savings,
                        'uncertain': any(p['uncertain'] for p in parts)}
        revenue = revenues[venue]
        idle_days, sde_days = idle['daily'].get(venue, {}), sde.get(venue, {})
        missing_deduction = (D(revenue.get('lending_idle_tw_avg_usd', '0')) + D(revenue.get('amm_idle_usds_tw_avg_usd', '0')) > 0 and set(days)-idle_days.keys()) or (D(revenue.get('sd_share', '0')) > 0 and set(days)-sde_days.keys())
        excluded = revenue.get('cof_excluded', False) or all(a in history.idle_accounts for a in accounts)
        conditional_cost = None if missing_deduction else price_daily(
            {d: r['sky_principal'] for d, r in daily.items()}, rates, idle_days, sde_days, excluded=excluded)
        measured = measured_yields[venue]
        exposure = D(measured['average_exposure']) if measured['average_exposure'] is not None else None
        net = None if conditional_cost is None else D(revenue['revenue'])-conditional_cost
        output.append({'venue': venue, 'daily': daily,
            'status': 'conditional_research_only',
            'uncertain_days': [d for d, r in daily.items() if r['uncertain']],
            'capital_gap_days': [d for d, r in daily.items() if abs(r['capital_basis_difference']) > D('.01')],
            'average_sky_principal': sum((r['sky_principal'] for r in daily.values()), ZERO)/len(days),
            'average_savings_principal': sum((r['savings_principal'] for r in daily.values()), ZERO)/len(days),
            'conditional_sky_borrowing_cost': conditional_cost,
            'conditional_net_apy': annualized_yield(net, exposure, len(days)) if net is not None and exposure is not None else None,
            'gross_apy': measured['gross_apy'],
            'yield_denominator': measured['denominator'],
        })
    return {'prime': 'spark', 'start': str(start), 'end': str(end), 'publishable': False,
        'policy': 'Retained proportional Savings principal/interest repayment and refinancing policy; independently separated routes remain separated.',
        'net_scope': 'Prime-retained revenue less conditional Sky borrowing cost only; excludes Savings VSR financing expense and prime-level adjustments.',
        'limitations': ['Unmatched receipts are unknown, not earnings or assumed Sky draws.',
                       'Daily original-capital differences remain explicit; no residual is assigned to force reconciliation.',
                       'Gross APY reuses settled revenue/exposure. Conditional net APY is not eligible for publication.'],
        'allocations': output}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('history', 'readiness', 'control', 'idle', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--venues', nargs='+', required=True)
    args = p.parse_args()
    inputs = [args.history, args.readiness, args.control, args.idle]
    if args.output.resolve() in {x.resolve() for x in inputs}:
        p.error('Output cannot overwrite evidence')
    ready = json.loads(gzip.decompress(args.readiness.read_bytes()))
    capital = next(r for r in ready['results'] if r['prime'] == 'spark')
    control, idle = json.loads(args.control.read_text()), json.loads(args.idle.read_text())
    if ready['input_sha256'].get(str(args.history)) != hashlib.sha256(args.history.read_bytes()).hexdigest():
        p.error('Funding replay must use the same normalized history as the capital control')
    control_hash = hashlib.sha256(args.control.read_bytes()).hexdigest()
    if ready['input_sha256'].get(str(args.control)) != control_hash or idle['control_sha256'] != control_hash:
        p.error('Control hashes do not match')
    with gzip.open(args.history, 'rt') as f:
        key = json.loads(next(f))['fingerprint']
    history = load_history(args.history, key)
    if history is None or not any(b.external_funding for b in history.batches):
        p.error('History must contain audited Savings funding operations')
    if len(set(args.venues)) != len(args.venues):
        p.error('Duplicate venue')
    accounts = {a for v in args.venues for a in
        ([history.venue_accounts[v]] if v in history.venue_accounts else []) + history.custody_accounts.get(v, [])}
    logging.basicConfig(level=logging.INFO)
    replay = replay_history(history, date.fromisoformat(capital['start']), date.fromisoformat(capital['end']), funding_accounts=accounts)
    result = summarize(replay, history, capital, control, idle, args.venues)
    result['input_sha256'] = {str(x): hashlib.sha256(x.read_bytes()).hexdigest() for x in inputs}
    result['source_sha256'] = {str(x): hashlib.sha256(x.read_bytes()).hexdigest() for x in
        [Path(__file__), *sorted(Path('src/settle/compute').glob('*.py')), Path('scripts/report_allocation_capital_readiness.py')]}
    args.output.write_text(json.dumps(result, indent=2, default=str)+'\n')


if __name__ == '__main__':
    main()
