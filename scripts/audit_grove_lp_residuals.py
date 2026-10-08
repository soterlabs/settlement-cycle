#!/usr/bin/env python3
"""Read-only proof of E11 LP valuation differences; never modify capital or CoF."""
import argparse
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

from settle.extract.transfer_logs import TRANSFER_TOPIC0

POOL = '0xe79c1c7e24755574438a26d5e062ad2626c04662'
HOLDER = '0x491edfb0b8b608044e227225c715981a30f3a44e'
ZERO = '0x' + '0'*40
COINS = {'0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
         '0x00000000efe302beaa2b3e6e1b18d08d69a9012a'}


def identify(groups, residuals):
    result = []
    for g in groups:
        # Match the existing Method B price: reserves at par / LP supply.
        # Curve's invariant-based virtual price is a different valuation.
        balances = list(map(D, g['balances']))
        supply = D(g['total_supply'])
        if len(balances) != 2 or min(balances) < 0 or supply <= 0:
            raise ValueError('Invalid E11 pinned pool state')
        rows = {}
        for r in g['rows']:
            if r['block_number'] != g['block']:
                raise ValueError('LP evidence is not pinned to the pool state block')
            key = r['transaction_hash'], r['log_index']
            if key in rows and rows[key] != r:
                raise ValueError('Conflicting LP evidence')
            rows[key] = r
        for r in rows.values():
            if r['address'] != POOL or r['topic0'] != TRANSFER_TOPIC0:
                continue
            sender, recipient = '0x'+r['topic1'][-40:], '0x'+r['topic2'][-40:]
            if (sender, recipient) == (ZERO, HOLDER):
                direction, payer, payee = 'deposit', HOLDER, POOL
            elif (sender, recipient) == (HOLDER, ZERO):
                direction, payer, payee = 'withdrawal', POOL, HOLDER
            else:
                continue
            tx = r['transaction_hash']
            cash = sum((D(int(t['data'], 16))/10**6 for t in rows.values()
                        if t['transaction_hash'] == tx and t['address'] in COINS
                        and t['topic0'] == TRANSFER_TOPIC0
                        and '0x'+t['topic1'][-40:] == payer
                        and '0x'+t['topic2'][-40:] == payee), D(0))
            units = D(int(r['data'], 16))
            nav = units*sum(balances)/10**6/supply
            difference = cash-nav if direction == 'deposit' else nav-cash
            key = 'ethereum:'+tx
            if cash <= 0 or key not in residuals or abs(D(residuals[key])-difference) >= D('0.00000001'):
                raise ValueError('LP cash and Method B valuation do not explain the residual')
            if any(x['capital_batch'] == key for x in result):
                raise ValueError('Multiple LP operations require explicit netting')
            result.append({'capital_batch': key, 'block': g['block'], 'direction': direction,
                'cash': str(cash), 'lp_raw_units': str(units), 'lp_value_at_pool_nav': str(nav),
                'valuation_difference': str(difference), 'raw_financing_residual': residuals[key],
                'normalization_rounding_difference': str(D(residuals[key])-difference)})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--events', type=Path, required=True)
    p.add_argument('--swap-audit', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    audit = json.loads(args.swap_audit.read_text())
    matches = identify(json.loads(args.events.read_text()), audit['remaining_residuals'])
    result = {'prime': 'grove', 'period': audit['period'],
        'scope': 'LP cash versus Method B NAV only; no capital or cost attribution change',
        'input_hashes': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in [args.events, args.swap_audit]},
        'identified_lp_valuation_differences': matches,
        'remaining_unexplained_outflows': {k:v for k,v in audit['remaining_residuals'].items()
            if k not in {m['capital_batch'] for m in matches}}}
    args.output.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
