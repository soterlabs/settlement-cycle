#!/usr/bin/env python3
"""Read-only period accrual from two independently checked Savings boundaries."""
import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

from audit_spark_savings_funding import savings_flows, summarize
from audit_spark_savings_liability import audit_vault


def period_accrual(groups, cash_events, opening):
    starts = {r['venue']: r for r in opening}
    if len(starts) != len(opening) or set(starts) != {g['venue'] for g in groups}:
        raise ValueError('Savings opening/closing venue scopes differ')
    pins = {}
    closing_cash_pins = {g['chain']: g['pin'] for g in cash_events}
    for g in groups:
        start = starts[g['venue']]
        if (start['chain'] != g['chain'] or start['vault'] != g['vault']
                or not start['pin'] < g['pin']):
            raise ValueError('Savings opening boundary differs from closing vault/chain')
        if closing_cash_pins.get(g['chain']) != g['pin']:
            raise ValueError('Savings closing cash and liability pins disagree')
        if g['chain'] in pins and pins[g['chain']] != start['pin']:
            raise ValueError('Savings opening chain pins disagree')
        pins[g['chain']] = start['pin']
    opening_cash = [{**g, 'pin': pins[g['chain']],
                     'rows': [r for r in g['rows'] if r['block_number'] <= pins[g['chain']]]}
                    for g in cash_events]
    totals = []
    for data in (opening_cash, cash_events):
        totals.append(summarize(savings_flows(data), {
            'unmatched_receipts': {}, 'unmatched_outflows': {},
        })['by_venue'])
    results = []
    for g in groups:
        start = starts[g['venue']]
        before = audit_vault({**g, **start,
                              'rows': [r for r in g['rows'] if r['block_number'] <= start['pin']]},
                             totals[0][g['venue']])
        after = audit_vault(g, totals[1][g['venue']])
        change = {k: str(D(after[k]) - D(before[k])) for k in (
            'accrued_vsr', 'share_rounding', 'other_cash_residual', 'net_cash_taken',
            'assets_outstanding',
        )}
        results.append({'venue': g['venue'], 'opening': before, 'closing': after,
                        'period_change': change})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('events', 'cash-events', 'opening-state', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    def read(path):
        raw = path.read_bytes()
        return json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)
    result = {
        'scope': 'Exact Savings period liability accrual, not Sky borrowing costs or a settlement revenue restatement.',
        'vaults': period_accrual(read(args.events), read(args.cash_events), read(args.opening_state)),
        'input_hashes': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (args.events, args.cash_events, args.opening_state)},
    }
    args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
