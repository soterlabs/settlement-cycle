#!/usr/bin/env python3
"""Rank audited allocation capital not yet attributed to original Sky draws.

Residual capital is not necessarily debt: it may be gains, external funding or
an untraced source. Coverage applies only to the explicitly selected allocations.
"""
import argparse
import gzip
import hashlib
import json
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_allocation_positions import audit
from audit_scoped_borrowing_origins import trace

from settle.compute.executed_spell_capital import apply_executed_spells
from settle.normalize.allocation_history_cache import load_history


def surviving_unclassified(local, origin):
    """Rank single-account contributions by cost still held, not gross turnover."""
    witnesses = {row['transaction']: row for row in origin['transactions']}
    factor = D(1)
    rows = []
    for event in reversed(local['transactions']):
        added = D(event['capital_added'])
        released = D(event['cost_basis_released'])
        if added and released:
            raise ValueError('Survival ranking requires a single position account')
        if added:
            residual = added - D(witnesses[event['transaction']]['conservative_draw_contribution'])
            if residual < -D('1e-8'):
                raise ValueError('Attributed draw exceeds contribution')
            remaining = max(D(0), residual) * factor
            if remaining:
                rows.append({'transaction': event['transaction'], 'day': event['day'],
                             'unclassified_contribution': str(max(D(0), residual)),
                             'fraction_still_held': str(factor),
                             'unclassified_closing_capital': str(remaining)})
        before = D(event['closing_capital']) - added + released
        if released:
            factor *= 1 - released / before
    rows.sort(key=lambda row: D(row['unclassified_closing_capital']), reverse=True)
    return rows


def coverage(history, groups, start, end):
    used = set()
    rows = []
    with localcontext() as ctx:
        ctx.prec = 60
        for group in groups:
            accounts = {history.venue_accounts[v] for v in group}
            for v in group:
                accounts.update(history.custody_accounts.get(v, []))
            if used & accounts:
                raise ValueError('Coverage groups overlap')
            used.update(accounts)
            local = audit(history, group[0], start, end, related_venues=group[1:])
            origin = trace(history, group, end)
            capital = D(local['closing_capital'])
            borrowed = sum(map(D, origin['closing_borrowed_by_account'].values()), D(0))
            if borrowed > capital + D('1e-8'):
                raise ValueError('Original borrowing exceeds allocation cost')
            surviving = surviving_unclassified(local, origin) if len(accounts) == 1 else None
            if surviving is not None and abs(sum(
                    (D(r['unclassified_closing_capital']) for r in surviving), D(0))
                    - (capital - borrowed)) > D('1e-8'):
                raise ValueError('Surviving source contributions do not reconcile')
            rows.append({'venues': list(group), 'closing_capital': str(capital),
                         'traced_borrowing': str(borrowed),
                         'remaining_origin_to_classify': str(max(D(0), capital - borrowed)),
                         'borrowed_percent': str(borrowed / capital * 100 if capital else D(0)),
                         'surviving_unclassified_sources': surviving,
                         'origin_trace': origin})
        rows.sort(key=lambda r: D(r['remaining_origin_to_classify']), reverse=True)
        capital = sum((D(r['closing_capital']) for r in rows), D(0))
        borrowed = sum((D(r['traced_borrowing']) for r in rows), D(0))
        return {'scope': __doc__.strip(), 'start': str(start), 'end': str(end),
                'selected_closing_capital': str(capital), 'selected_traced_borrowing': str(borrowed),
                'selected_remaining_origin_to_classify': str(capital - borrowed), 'allocations': rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history', type=Path, required=True)
    p.add_argument('--groups', nargs='+', required=True, help='Venue IDs; comma-separated for grouped custody.')
    p.add_argument('--start', type=date.fromisoformat, required=True)
    p.add_argument('--end', type=date.fromisoformat, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--spark-draw-receipts', type=Path,
                   help='Optional receipt evidence for isolated Spark USDS draw routes.')
    args = p.parse_args()
    with gzip.open(args.history, 'rt') as f:
        key = json.loads(next(f))['fingerprint']
    history = load_history(args.history, key)
    if history is None:
        raise ValueError('Invalid history')
    history = apply_executed_spells(history)
    proofs = []
    if args.spark_draw_receipts:
        from audit_spark_draw_routes import separate
        opener = gzip.open if args.spark_draw_receipts.suffix == '.gz' else open
        with opener(args.spark_draw_receipts, 'rt') as f:
            history, proofs = separate(history, json.load(f))
    r = coverage(history, [g.split(',') for g in args.groups], args.start, args.end)
    r['isolated_draw_routes'] = proofs
    if args.spark_draw_receipts:
        r['receipt_sha256'] = hashlib.sha256(args.spark_draw_receipts.read_bytes()).hexdigest()
    r['input_sha256'] = hashlib.sha256(args.history.read_bytes()).hexdigest()
    paths = [Path(__file__), Path('scripts/audit_allocation_positions.py'),
             Path('scripts/audit_scoped_borrowing_origins.py'),
             Path('scripts/audit_spark_draw_routes.py'),
             *sorted(Path('src/settle/compute').glob('*.py'))]
    r['source_sha256'] = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in paths}
    data = (json.dumps(r, indent=2) + '\n').encode()
    args.output.write_bytes(gzip.compress(data, mtime=0) if args.output.suffix == '.gz' else data)
    print(json.dumps([{k: v for k, v in row.items() if k != 'origin_trace'} for row in r['allocations']], indent=2))


if __name__ == '__main__':
    main()
