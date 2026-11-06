#!/usr/bin/env python3
"""Allocation funding origin with upstream accounts, custody ranking and monthly snapshots.

All percentages describe acquisition capital at each month-end, not outstanding
ilk debt. Other funding, gains and untraced principal remain unclassified.
"""
import argparse
import calendar
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_allocation_positions import audit
from audit_origin_custody_routes import separate
from audit_scoped_borrowing_origins import trace

from settle.compute.executed_spell_capital import apply_executed_spells
from settle.normalize.allocation_history_cache import load_history

EPS = D('1e-8')


def rank_residual_sources(history, accounts, local, origin, end):
    """Carry residual contribution lots through custody using weighted-average releases.

Check against the independently computed capital minus borrowed basis after
EVERY target event. Fail rather than assign a residual using incompatible
clearing conventions in a mixed transaction.
    """
    lots = {a: defaultdict(D) for a in accounts}
    events = {e['transaction']: e for e in local['transactions']}
    witnesses = {e['transaction']: e for e in origin['transactions']}
    dates = {}
    previous = D(0)
    for b in sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index)):
        if b.day > end:
            break
        if b.identity not in events:
            continue
        dates[b.identity] = str(b.day)
        row = witnesses[b.identity]
        current = sum((D(row['borrowed_by_account'][a]) for a in accounts), D(0))
        incoming, removed = [], defaultdict(D)
        out = D(0)
        for m in b.movements:
            if m.account not in accounts:
                continue
            change = m.change - m.external_income
            if change < 0:
                fraction = min(D(1), -change / (m.value_before + m.external_income))
                for tx, amount in list(lots[m.account].items()):
                    released = amount * fraction
                    lots[m.account][tx] -= released
                    removed[tx] += released
                out -= change
            elif change > 0:
                incoming.append((m.account, change))
        received = sum((amount for _, amount in incoming), D(0))
        kept = min(D(1), received / out) if out else D(0)
        released_borrowed = sum((D(row['withdrawn_borrowed_basis'].get(a, '0')) for a in accounts), D(0))
        borrowed_added = current - previous + released_borrowed * (1 - kept)
        unknown_added = D(events[b.identity]['capital_added']) - borrowed_added
        if unknown_added < -EPS:
            raise ValueError(f'Custody residual requires separately evidenced mixed routing: {b.identity} {unknown_added}')
        for a, amount in incoming:
            fraction = amount / received
            for tx, value in removed.items():
                lots[a][tx] += value * kept * fraction
            lots[a][b.identity] += max(D(0), unknown_added) * fraction
        expected = D(events[b.identity]['closing_capital']) - current
        actual = sum((sum(values.values(), D(0)) for values in lots.values()), D(0))
        if abs(actual - expected) > EPS:
            raise ValueError('Custody residual lots differ from capital less borrowed origin')
        previous = current
    totals = defaultdict(D)
    for values in lots.values():
        for tx, amount in values.items():
            totals[tx] += amount
    return sorted([{'transaction': tx, 'day': dates[tx], 'unclassified_closing_capital': str(amount)}
                   for tx, amount in totals.items() if amount > EPS],
                  key=lambda row: D(row['unclassified_closing_capital']), reverse=True)


def month_ends(start, end):
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        day = date(year, month, calendar.monthrange(year, month)[1])
        if start <= day <= end:
            yield day
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def snapshots(local, origin, accounts, start, end):
    # Witnesses are chronological and include pre-period opening history.
    rows, cursor, closing = origin['transactions'], 0, D(0)
    result = []
    for day in month_ends(start, end):
        while cursor < len(rows) and rows[cursor]['day'] <= str(day):
            closing = sum((D(rows[cursor]['borrowed_by_account'][a]) for a in accounts), D(0))
            cursor += 1
        capital = D(local['daily'][str(day)])
        if closing < -EPS or closing > capital + EPS:
            raise ValueError('Monthly borrowed origin exceeds acquisition capital')
        result.append({'month_end': str(day), 'capital': str(capital), 'borrowed': str(closing),
                       'unclassified': str(max(D(0), capital - closing)),
                       'borrowed_percent': str(closing / capital * 100) if capital else None})
    return result


def attribute(history, venue, group, start, end, *, bound_external_funding=False):
    with localcontext() as ctx:
        ctx.prec = 60
        if venue not in group:
            raise ValueError('Target must belong to origin group')
        history, custody_routes = separate(history, group)
        accounts = {history.venue_accounts[venue], *history.custody_accounts.get(venue, [])}
        origin = trace(history, group, end, bound_external_funding=bound_external_funding)
        local = audit(history, venue, start, end)
        monthly = snapshots(local, origin, accounts, start, end)
        residuals = rank_residual_sources(history, accounts, local, origin, end)
        return {'scope': __doc__.strip(), 'venue': venue, 'origin_group': list(group),
                'start': str(start), 'end': str(end), 'monthly': monthly,
                'closing_capital': local['closing_capital'],
                'closing_borrowed': str(sum((D(origin['closing_borrowed_by_account'][a]) for a in accounts), D(0))),
                'remaining_sources': residuals, 'custody_routes': custody_routes, 'origin_trace': origin}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history', type=Path, required=True)
    p.add_argument('--venue', required=True)
    p.add_argument('--group', nargs='+', required=True)
    p.add_argument('--start', type=date.fromisoformat, required=True)
    p.add_argument('--end', type=date.fromisoformat, required=True)
    p.add_argument('--bound-external-funding', action='store_true')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.resolve() == args.history.resolve():
        p.error('Output must not overwrite history')
    with gzip.open(args.history, 'rt') as f:
        key = json.loads(next(f))['fingerprint']
    history = load_history(args.history, key)
    if history is None:
        raise ValueError('Invalid history')
    result = attribute(apply_executed_spells(history), args.venue, args.group, args.start, args.end,
                       bound_external_funding=args.bound_external_funding)
    result['input_sha256'] = hashlib.sha256(args.history.read_bytes()).hexdigest()
    paths = [Path(__file__), Path('scripts/audit_scoped_borrowing_origins.py'),
             Path('scripts/audit_allocation_positions.py'), Path('scripts/audit_origin_custody_routes.py'), *sorted(Path('src/settle/compute').glob('*.py'))]
    result['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    data = (json.dumps(result, indent=2)+'\n').encode()
    args.output.write_bytes(gzip.compress(data, mtime=0) if args.output.suffix == '.gz' else data)
    print(json.dumps(result['monthly'], indent=2))


if __name__ == '__main__':
    main()
