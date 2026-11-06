#!/usr/bin/env python3
"""Trace original draws through authenticated CCTP claims and allocation cash.

Normalized message links establish the route, not the origin of pre-existing
source cash. Selected claim/cash/position movements carry weighted-average
borrowed basis. Untraced funding and gains are not silently labeled Sky debt.
Pre-period movements establish opening origin only; no settlements change.
"""
import argparse
import gzip
import hashlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_allocation_positions import audit
from audit_scoped_borrowing_origins import trace

from settle.compute.executed_spell_capital import apply_executed_spells
from settle.normalize.allocation_history_cache import load_history

BRIDGES = ('cctp:', 'cctpv2:')


def surviving_sources(local, origin, target):
    witnesses = {row['transaction']: row for row in origin['transactions']}
    previous = D(0)
    contributions = {}
    for event in local['transactions']:
        current = D(witnesses[event['transaction']]['borrowed_by_account'][target])
        added = D(event['capital_added'])
        if added:
            if D(event['cost_basis_released']):
                raise ValueError('Source ranking requires a single position account')
            attributed = current - previous
            if attributed < -D('1e-8') or attributed > added + D('1e-8'):
                raise ValueError('Borrowed contribution exceeds acquired capital')
            contributions[event['transaction']] = max(D(0), added - attributed)
        previous = current
    factor = D(1)
    rows = []
    for event in reversed(local['transactions']):
        added, released, closing = (D(event[k]) for k in (
            'capital_added', 'cost_basis_released', 'closing_capital'))
        remaining = contributions.get(event['transaction'], D(0)) * factor
        if remaining:
            rows.append({'transaction': event['transaction'], 'day': event['day'],
                         'unclassified_contribution': str(contributions[event['transaction']]),
                         'fraction_still_held': str(factor),
                         'unclassified_closing_capital': str(remaining)})
        if released:
            factor *= 1 - released / (closing - added + released)
    rows.sort(key=lambda row: D(row['unclassified_closing_capital']), reverse=True)
    return rows


def attribute(history, venue, cash_venue, start, end, *, bound_external_funding=False):
    with localcontext() as ctx:
        ctx.prec = 60
        return _attribute(history, venue, cash_venue, start, end, bound_external_funding)


def _attribute(history, venue, cash_venue, start, end, bound_external_funding):
    target = history.venue_accounts[venue]
    cash = history.venue_accounts[cash_venue]
    if target == cash:
        raise ValueError('Cash and allocation must differ')
    claims = {m.account for b in history.batches if b.day <= end
              and any(x.account in {target, cash} and x.change > 0 for x in b.movements)
              for m in b.movements if m.change < 0 and m.account.startswith(BRIDGES)}
    by_claim = {claim: [] for claim in claims}
    for b in history.batches:
        if b.day > end:
            continue
        for m in b.movements:
            if m.account in claims and m.change:
                by_claim[m.account].append((b, m))
    links = []
    for claim in sorted(claims):
        legs = sorted(by_claim[claim], key=lambda x: (x[0].timestamp, x[0].chain,
                                                     x[0].block, x[0].log_index))
        if len(legs) != 2:
            raise ValueError('Bridge claim requires exactly one creation and one receipt')
        (source, sent), (receipt, received) = legs
        if (sent.change <= 0 or sent.value_before or sent.external_income
                or received.change != -sent.change or received.value_before != sent.change
                or received.external_income or source.identity == receipt.identity
                or source.chain == receipt.chain or source.timestamp >= receipt.timestamp):
            raise ValueError('Bridge source/receipt amount or order mismatch')
        links.append({'claim': claim, 'amount': str(sent.change),
                      'source': source.identity, 'receipt': receipt.identity,
                      'source_day': str(source.day), 'receipt_day': str(receipt.day)})
    aliases = {f'bridge-origin:{i}': claim for i, claim in enumerate(sorted(claims))}
    if set(aliases) & history.venue_accounts.keys():
        raise ValueError('Bridge alias collides with venue')
    scoped = replace(history, venue_accounts={**history.venue_accounts, **aliases})
    origin = trace(scoped, (venue, cash_venue, *aliases), end, snapshot_accounts={target, cash},
                   bound_external_funding=bound_external_funding)
    local = audit(history, venue, start, end)
    capital = D(local['closing_capital'])
    accounts = {target, *history.custody_accounts.get(venue, [])}
    borrowed = sum((D(origin['closing_borrowed_by_account'][a]) for a in accounts), D(0))
    if borrowed > capital + D('1e-8'):
        raise ValueError('Borrowed origin exceeds local allocation capital')
    surviving = surviving_sources(local, origin, target) if len(accounts) == 1 else None
    if surviving is not None and abs(sum(
            (D(row['unclassified_closing_capital']) for row in surviving), D(0))
            - (capital - borrowed)) > D('1e-8'):
        raise ValueError('Remaining source contributions do not reconcile')
    source_witness = {r['transaction']: r for r in origin['transactions']}
    for link in links:
        link['borrowed_at_receipt'] = origin['withdrawals'][link['receipt']][link['claim']]
        link['source_draw_contribution_to_group'] = source_witness[link['source']]['conservative_draw_contribution']
    return {'scope': __doc__.strip(), 'venue': venue, 'cash_venue': cash_venue,
            'start': str(start), 'end': str(end), 'closing_capital': str(capital),
            'closing_borrowed': str(borrowed), 'remaining_origin_to_classify': str(capital - borrowed),
            'borrowed_percent': str(borrowed / capital * 100 if capital else D(0)),
            'surviving_unclassified_sources': surviving,
            'bridge_links': links, 'origin_trace': origin}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history', type=Path, required=True)
    p.add_argument('--venue', required=True)
    p.add_argument('--cash-venue', required=True)
    p.add_argument('--start', type=date.fromisoformat, required=True)
    p.add_argument('--end', type=date.fromisoformat, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--bound-external-funding', action='store_true',
                   help='Bound Sky funding after all other uses, including external funding cash payments.')
    args = p.parse_args()
    if args.history.resolve() == args.output.resolve():
        p.error('Output must not overwrite history')
    with gzip.open(args.history, 'rt') as f:
        key = json.loads(next(f))['fingerprint']
    h = load_history(args.history, key)
    if h is None:
        raise ValueError('Invalid history')
    r = attribute(apply_executed_spells(h), args.venue, args.cash_venue, args.start, args.end,
                  bound_external_funding=args.bound_external_funding)
    r['input_sha256'] = hashlib.sha256(args.history.read_bytes()).hexdigest()
    paths = [Path(__file__), Path('scripts/audit_allocation_positions.py'),
             Path('scripts/audit_scoped_borrowing_origins.py'),
             *sorted(Path('src/settle/compute').glob('*.py'))]
    r['source_sha256'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    data = (json.dumps(r, indent=2) + '\n').encode()
    args.output.write_bytes(gzip.compress(data, mtime=0) if args.output.suffix == '.gz' else data)
    print(json.dumps({k: v for k, v in r.items() if k not in (
        'origin_trace', 'bridge_links', 'source_sha256', 'surviving_unclassified_sources')}, indent=2))


if __name__ == '__main__':
    main()
