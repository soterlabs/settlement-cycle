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
from types import SimpleNamespace

from report_allocation_capital_readiness import price_daily
from report_allocation_yields import build as build_yields

from settle.compute.allocation_capital import replay_history
from settle.compute.allocation_financing import annualized_yield
from settle.compute.allocation_reconciliation import validate_idle_control
from settle.normalize.allocation_history_cache import load_history

ZERO = D(0)


def summarize(replay, history, readiness, control, idle, venues, anchorage=None):
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
    measured_yields = {r['venue']: r for r in build_yields({'results': [readiness]}, {'spark': control}, anchorage)['results'][0]['allocations']}
    output = []
    for venue in venues:
        if venue in history.covered_by_boundary:
            raise ValueError('Custody covered by another venue cannot be charged again')
        accounts = ([history.venue_accounts[venue]] if venue in history.venue_accounts else []) + history.custody_accounts.get(venue, [])
        if not accounts or venue not in capitals:
            raise ValueError('Missing selected venue capital')
        daily = {}
        for d in days:
            observed = replay.daily_funding[date.fromisoformat(d)]
            if len(set(accounts)) != len(accounts):
                raise ValueError('Duplicate allocation custody account')
            parts = [observed[a] for a in accounts]
            components = [x for part in parts for x in
                          (part['sky_principal'], *part['savings_by_source'].values())]
            if any(not x.is_finite() or x < 0 for x in components):
                raise ValueError('Invalid individual funding component')
            sky = sum((p['sky_principal'] for p in parts), ZERO)
            savings = sum((sum(p['savings_by_source'].values(), ZERO) for p in parts), ZERO)
            original = D(capitals[venue]['daily_principal'][d])
            if any(not x.is_finite() or x < 0 for x in (sky, savings, original)):
                raise ValueError('Invalid funding component')
            daily[d] = {'sky_principal': sky, 'savings_principal': savings,
                        'original_capital_control': original,
                        'capital_basis_difference': original-sky-savings,
                        'uncertain': any(p['uncertain'] for p in parts)}
        revenue = revenues.get(venue, {})
        idle_days, sde_days = idle['daily'].get(venue, {}), sde.get(venue, {})
        missing_deduction = (D(revenue.get('lending_idle_tw_avg_usd', '0')) + D(revenue.get('amm_idle_usds_tw_avg_usd', '0')) > 0 and set(days)-idle_days.keys()) or (D(revenue.get('sd_share', '0')) > 0 and set(days)-sde_days.keys())
        excluded = revenue.get('cof_excluded', False) or all(a in history.idle_accounts for a in accounts)
        conditional_cost = None if not revenue or missing_deduction else price_daily(
            {d: r['sky_principal'] for d, r in daily.items()}, rates, idle_days, sde_days, excluded=excluded)
        measured = measured_yields[venue]
        exposure = D(measured['average_exposure']) if measured['average_exposure'] is not None else None
        net = None if conditional_cost is None else D(revenue['revenue'])-conditional_cost
        output.append({'venue': venue, 'daily': daily,
            'status': 'conditional_research_only' if revenue else 'funding_only_missing_revenue',
            'missing_revenue': not bool(revenue),
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


def hashes(paths):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def write_json(path, value, *, compressed=False):
    raw = (json.dumps(value, indent=2, default=str)+'\n').encode()
    temporary = path.with_name(path.name+'.tmp')
    try:
        temporary.write_bytes(gzip.compress(raw, mtime=0) if compressed else raw)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_snapshot(path, identity, replay):
    write_json(path, {'identity': identity, 'daily_funding': {
        str(day): accounts for day, accounts in replay.daily_funding.items()}}, compressed=True)


def load_snapshot(path, identity):
    if not path.exists():
        return None
    saved = json.loads(gzip.decompress(path.read_bytes()))
    if saved['identity'] != identity:
        raise ValueError('Replay snapshot belongs to different inputs or replay code; use a new path')
    daily = {}
    start, end = date.fromisoformat(identity['start']), date.fromisoformat(identity['end'])
    expected = {str(start+timedelta(days=i)) for i in range((end-start).days+1)}
    if set(saved['daily_funding']) != expected:
        raise ValueError('Incomplete replay snapshot days')
    for day, accounts in saved['daily_funding'].items():
        if set(accounts) != set(identity['accounts']):
            raise ValueError('Incomplete replay snapshot accounts')
        daily[date.fromisoformat(day)] = {}
        for account, row in accounts.items():
            if type(row['uncertain']) is not bool:
                raise ValueError('Invalid funding uncertainty flag')
            values = [D(row['sky_principal']), D(row['observed_value']),
                      *map(D, row['savings_by_source'].values())]
            if any(not x.is_finite() or x < 0 for x in values):
                raise ValueError('Invalid replay snapshot amount')
            daily[date.fromisoformat(day)][account] = dict(row,
                sky_principal=values[0], observed_value=values[1],
                savings_by_source={k: D(v) for k, v in row['savings_by_source'].items()})
    return SimpleNamespace(daily_funding=daily)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('history', 'readiness', 'control', 'idle', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--venues', nargs='+', required=True)
    p.add_argument('--anchorage-exposure', type=Path,
                   help='Audited daily outstanding loan principal, never escrow cash')
    p.add_argument('--replay-snapshot', type=Path,
                   help='Reusable compact daily funding snapshots; default beside output')
    args = p.parse_args()
    inputs = [args.history, args.readiness, args.control, args.idle]
    snapshot_path = args.replay_snapshot or args.output.with_suffix('.replay.json.gz')
    if snapshot_path.resolve() in {x.resolve() for x in [*inputs, args.output]}:
        p.error('Replay snapshot cannot overwrite evidence or the report')
    if args.output.resolve() in {x.resolve() for x in inputs}:
        p.error('Output cannot overwrite evidence')
    ready = json.loads(gzip.decompress(args.readiness.read_bytes()))
    capital = next(r for r in ready['results'] if r['prime'] == 'spark')
    control, idle = json.loads(args.control.read_text()), json.loads(args.idle.read_text())
    anchorage_path = args.anchorage_exposure or (
        Path('reconciliation/spark_anchorage_receipt_funding_origins_2026_08.json.gz')
        if capital['start'] == '2026-08-01' and capital['end'] == '2026-08-31' else None)
    if anchorage_path:
        if anchorage_path.resolve() in {args.output.resolve(), snapshot_path.resolve()}:
            p.error('Outputs cannot overwrite Anchorage evidence')
        inputs.append(anchorage_path)
    anchorage = json.loads(gzip.decompress(anchorage_path.read_bytes())) if anchorage_path else None
    # Freeze provenance before any long-running work, not after an operator
    # may have changed the checkout or evidence while the replay was running.
    input_hashes = hashes(inputs)
    replay_sources = [*sorted(Path('src/settle/compute').glob('*.py')),
        Path('src/settle/normalize/allocation_capital.py'),
        Path('src/settle/normalize/allocation_history_cache.py'),
        Path('config/capital-tracing/spark-par-swap-gains.json')]
    source_hashes = hashes([Path(__file__), *replay_sources,
        Path('scripts/report_allocation_capital_readiness.py'), Path('scripts/report_allocation_yields.py')])
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
    # Capture all measured accounts once so adding another venue to the report
    # does not require another inception-to-August replay.
    accounts = set(history.venue_accounts.values()) | {
        a for values in history.custody_accounts.values() for a in values}
    identity = {'schema': 1, 'history_sha256': input_hashes[str(args.history)],
                'start': capital['start'], 'end': capital['end'],
                'accounts': sorted(accounts), 'replay_source_sha256': hashes(replay_sources)}
    logging.basicConfig(level=logging.INFO)
    replay = load_snapshot(snapshot_path, identity)
    if replay is None:
        replay = replay_history(history, date.fromisoformat(capital['start']), date.fromisoformat(capital['end']), funding_accounts=accounts)
        if hashes(inputs) != input_hashes or hashes(map(Path, source_hashes)) != source_hashes:
            raise ValueError('Evidence or code changed during replay')
        save_snapshot(snapshot_path, identity, replay)
    result = summarize(replay, history, capital, control, idle, args.venues, anchorage)
    if hashes(inputs) != input_hashes or hashes(map(Path, source_hashes)) != source_hashes:
        raise ValueError('Evidence or code changed during report generation')
    result['input_sha256'] = input_hashes
    result['source_sha256'] = source_hashes
    write_json(args.output, result)



if __name__ == '__main__':
    main()
