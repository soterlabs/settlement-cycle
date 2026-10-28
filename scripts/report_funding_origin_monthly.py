#!/usr/bin/env python3
"""Month-end funding-origin snapshots and complete configured-venue audit inventory.

This reports reviewed allocation-specific methods, not a consolidated debt or
borrowing-cost reconciliation. Earlier months use evidence available in these
snapshots; unproven capital is not assumed borrowed. No production data changes.
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
from audit_scoped_allocation_origin import month_ends, snapshots

from settle.compute.executed_spell_capital import apply_executed_spells
from settle.domain.config import load_prime_by_id
from settle.normalize.allocation_history_cache import load_history

START, END = date(2026, 1, 1), date(2026, 8, 31)
SOURCES = {
    'spark:S1': 'reconciliation/spark_spusds_receipt_funding_origins_2026_08.json.gz',
    'spark:S32': 'reconciliation/spark_susds_all_month_origins_2026_08.json.gz',
    'spark:S34': 'reconciliation/spark_base_morpho_funding_origins_2026_08.json.gz',
    'spark:S23': 'reconciliation/spark_anchorage_funding_origins_2026_08.json',
    'grove:E9': 'reconciliation/grove_jtrsy_funding_origins_2026_08.json.gz',
    'grove:E10': 'reconciliation/grove_large_allocation_origin_coverage_2026_08.json.gz',
    'grove:E8,E20': 'reconciliation/grove_large_allocation_origin_coverage_2026_08.json.gz',
    'grove:E42': 'reconciliation/grove_warehouse_funding_origins_2026_08.json',
    'grove:E23': 'reconciliation/grove_steakhouse_funding_origins_2026_08.json',
}


def read(path):
    p = Path(path)
    return json.loads(gzip.decompress(p.read_bytes()) if p.suffix == '.gz' else p.read_bytes())


def row(day, capital, borrowed, gains=D(0)):
    if borrowed < 0 or gains < 0 or borrowed + gains > capital + D('1e-8'):
        raise ValueError('Monthly origin components exceed capital')
    return {'month_end': str(day), 'capital': str(capital), 'borrowed': str(borrowed),
            'identified_gains': str(gains), 'unclassified': str(max(D(0), capital - borrowed - gains)),
            'borrowed_percent': str(borrowed / capital * 100) if capital else None}


def report(histories):
    with localcontext() as ctx:
        ctx.prec = 60
        allocations = []
        for key, path in SOURCES.items():
            prime, venue_ids = key.split(':')
            venues = venue_ids.split(',')
            source = read(path)
            if 'monthly' in source:
                monthly = source['monthly']
                method = 'scoped origin with upstream/custody attribution'
            elif 'closing_facility_principal' in source:
                monthly = [row(day, D(source['daily'][str(day)]['facility_principal']),
                               D(source['daily'][str(day)]['direct_origin_remaining_before_refinancing']))
                           for day in month_ends(START, END)]
                method = 'facility principal-return policy; direct origin before refinancing'
            elif 'closing_origin' in source:
                monthly, cursor, current = [], 0, {'borrowed': '0', 'reinvested_gains': '0', 'unresolved': '0'}
                events = source['transactions']
                for day in month_ends(START, END):
                    while cursor < len(events) and events[cursor]['day'] <= str(day):
                        current = events[cursor]['closing_origin']
                        cursor += 1
                    capital = sum(map(D, current.values()), D(0))
                    monthly.append(row(day, capital, D(current['borrowed']), D(current['reinvested_gains'])))
                method = 'bridge/cash origin; separately identified reinvested gains'
            else:
                origin = (next(a for a in source['allocations'] if a['venues'] == venues)['origin_trace']
                          if 'allocations' in source else source['origin_trace'])
                h = histories[prime]
                local = audit(h, venues[0], START, END, related_venues=venues[1:])
                accounts = {h.venue_accounts[v] for v in venues}
                for v in venues:
                    accounts.update(h.custody_accounts.get(v, []))
                monthly = snapshots(local, origin, accounts, START, END)
                method = 'reviewed origin ledger; month-end acquisition capital'
            if len(monthly) != 8:
                raise ValueError('Missing month-end coverage')
            allocations.append({'prime': prime, 'venues': venues, 'method': method,
                                'source': path, 'monthly': monthly})
        inventory = []
        for prime, h in histories.items():
            for v in load_prime_by_id(prime).venues:
                audited = next((a for a in allocations if a['prime'] == prime and v.id in a['venues']), None)
                status = ('audited' if audited else 'not_in_normalized_snapshot'
                          if v.id not in h.venue_accounts else 'not_audited')
                inventory.append({'prime': prime, 'venue': v.id, 'label': v.label,
                                  'status': status, 'report_group': audited['venues'] if audited else None,
                                  'normalization_note': h.unsupported.get(v.id),
                                  'account': h.venue_accounts.get(v.id)})
        return {'scope': __doc__.strip(), 'start': str(START), 'end': str(END),
                'allocations': allocations, 'inventory': inventory}


def markdown(result):
    lines = ['# Funding-origin coverage: January-August 2026', '',
             'Month-end acquisition capital, not monthly average balances or outstanding ilk debt.',
             'Methods are allocation-specific; no Prime-wide reconciliation percentage is asserted.', '',
             '## Traced borrowing as a percentage of allocation capital', '',
             '| Prime / allocation | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug |',
             '|---|' + '---:|' * 8]
    for a in result['allocations']:
        cells = ['—' if D(r['capital']) == 0 else 'dust' if D(r['capital']) < 1
                 else f"{D(r['borrowed_percent']):.2f}%" for r in a['monthly']]
        lines.append('| ' + a['prime'] + ' ' + '/'.join(a['venues']) + ' | ' + ' | '.join(cells) + ' |')
    lines += ['', '## Capital whose origin remains unclassified (USD millions)', '',
              '| Prime / allocation | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug |',
              '|---|' + '---:|' * 8]
    for a in result['allocations']:
        lines.append('| ' + a['prime'] + ' ' + '/'.join(a['venues']) + ' | ' +
                     ' | '.join(f"{D(r['unclassified']) / 1000000:.3f}" for r in a['monthly']) + ' |')
    lines += ['', 'E23 separately identifies reinvested gains; these are excluded from its unresolved column.',
              'Zero capital is shown as —; balances below $1 are shown as dust. Exact amounts remain in the JSON.',
              'S34 holds only dust at January-July month ends; percentages on those amounts are not meaningful.',
              'S32 now includes receipt research across all eight month ends; remaining unclassified capital still needs investigation.', '',
              '## Configured venue inventory', '',
              '“Not audited” means no reviewed allocation-specific origin result in this report, not zero borrowed funding.',
              'A missing normalized account requires extraction/support assessment before attribution.', '',
              '| Prime | Venue | Allocation | Status |', '|---|---|---|---|']
    for r in result['inventory']:
        lines.append(f"| {r['prime']} | {r['venue']} | {r['label']} | {r['status'].replace('_', ' ')} |")
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spark-history', type=Path, required=True)
    p.add_argument('--grove-history', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--markdown', type=Path, required=True)
    args = p.parse_args()
    inputs = [args.spark_history, args.grove_history, *(Path(p) for p in SOURCES.values())]
    if args.output.resolve() == args.markdown.resolve() or any(
            out.resolve() == source.resolve() for out in (args.output, args.markdown) for source in inputs):
        p.error('Outputs must be distinct from inputs and one another')
    histories = {}
    for prime, path in [('spark', args.spark_history), ('grove', args.grove_history)]:
        with gzip.open(path, 'rt') as f:
            key = json.loads(next(f))['fingerprint']
        h = load_history(path, key)
        if h is None:
            raise ValueError('Invalid history')
        histories[prime] = apply_executed_spells(h)
    result = report(histories)
    result['input_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    paths = [Path(__file__), Path('scripts/audit_scoped_allocation_origin.py'),
             Path('scripts/audit_allocation_positions.py'), Path('config/spark.yaml'), Path('config/grove.yaml'),
             *sorted(Path('src/settle/compute').glob('*.py'))]
    result['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    args.markdown.write_text(markdown(result))
    print('Reported',len(result['allocations']),'allocation groups and',len(result['inventory']),'configured venues.')


if __name__ == '__main__':
    main()
