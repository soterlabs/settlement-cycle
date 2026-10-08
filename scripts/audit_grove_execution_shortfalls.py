#!/usr/bin/env python3
"""Explain raw capital-replay residuals using actual swap execution evidence.

Read-only diagnostic. Never assign a borrowing cost, alter a capital movement,
or infer that the whole execution shortfall was debt-funded. Canonical event
and token-transfer records must accompany the financing replay result.
"""
import argparse
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from settle.extract.aave_reconstruct import _words
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_curve_swaps import COINS, EXCHANGE, HOLDER, POOL, curve_swap_income


def curve_shortfalls(rows):
    rows = [LogRow(**r) for r in rows]
    curve_swap_income(rows)  # Authenticates all swap amounts, including losses.
    seen, result = set(), []
    for r in rows:
        if (r.address != POOL or r.topic0 != EXCHANGE
                or not r.topic1.endswith(HOLDER[2:]) or (r.transaction_hash, r.log_index) in seen):
            continue
        seen.add((r.transaction_hash, r.log_index))
        i, sold, j, bought = _words(r.data)
        paid, received = D(sold)/10**COINS[i][1], D(bought)/10**COINS[j][1]
        if paid > received:
            result.append({'transaction_hash': r.transaction_hash, 'block': r.block_number,
                'log_index': r.log_index, 'pool': POOL, 'holder': HOLDER,
                'paid_token': COINS[i][0], 'paid': str(paid),
                'received_token': COINS[j][0], 'received': str(received),
                'execution_shortfall': str(paid - received)})
    return result


UNISWAP_POOL = '0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d'
UNISWAP_SWAP = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'
UNISWAP_COINS = ('0x00000000efe302beaa2b3e6e1b18d08d69a9012a',
                 '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48')
PAU = '0x0dcd9298e163dfd3c0b5b00f0d9093c36e40a153'


def uniswap_shortfalls(rows):
    # Configured E12 AUSD/USDC pool, also used by the PAU. Both coins have
    # six decimals. The Swap sender can be the router; actual ALM payment
    # and receipt must match, so another trader's swap cannot be attributed.
    unique = {}
    for r in rows:
        key = r['transaction_hash'], r['log_index']
        if key in unique and unique[key] != r:
            raise ValueError('Conflicting Uniswap event identity')
        unique[key] = r
    expected, actual = defaultdict(int), defaultdict(int)
    result = []
    for r in unique.values():
        if r['address'] in UNISWAP_COINS and r['topic0'] == TRANSFER_TOPIC0:
            if len(r['data']) != 66:
                raise ValueError('Invalid Uniswap token transfer')
            actual[(r['transaction_hash'], r['address'], '0x'+r['topic1'][-40:],
                    '0x'+r['topic2'][-40:])] += int(r['data'], 16)
        if r['address'] != UNISWAP_POOL or r['topic0'] != UNISWAP_SWAP:
            continue
        holder = '0x'+r['topic2'][-40:]
        if holder not in (HOLDER, PAU):
            continue
        w = _words(r['data'])
        if len(w) != 5:
            raise ValueError('Invalid Uniswap Swap layout')
        amounts = [n-2**256 if n >= 2**255 else n for n in w[:2]]
        if amounts[0]*amounts[1] >= 0:
            raise ValueError('Uniswap swap must have one payment and one receipt')
        i = 0 if amounts[0] > 0 else 1
        j = 1-i
        paid, received = D(amounts[i])/10**6, D(-amounts[j])/10**6
        expected[(r['transaction_hash'], UNISWAP_COINS[i], holder, UNISWAP_POOL)] += amounts[i]
        expected[(r['transaction_hash'], UNISWAP_COINS[j], UNISWAP_POOL, holder)] -= amounts[j]
        if paid > received:
            result.append({'transaction_hash': r['transaction_hash'], 'block': r['block_number'],
                'log_index': r['log_index'], 'pool': UNISWAP_POOL, 'holder': holder,
                'paid_token': UNISWAP_COINS[i], 'paid': str(paid),
                'received_token': UNISWAP_COINS[j], 'received': str(received),
                'execution_shortfall': str(paid-received)})
    if any(actual[k] != value for k,value in expected.items()):
        raise ValueError('Uniswap swap differs from actual ALM transfers')
    return result


def classify(financing, shortfalls, period):
    date.fromisoformat(period + '-01')
    residuals = financing['unmatched_outflows']
    matched, other, used = [], [], set()
    for record in shortfalls:
        # Reviewed adapters suffix some identities (e.g. STAC subscriptions).
        # Match the exact underlying transaction, never a date/amount search.
        candidates = [k for k in residuals if k.split(':')[1] == record['transaction_hash']
                      and abs(D(residuals[k])-D(record['execution_shortfall'])) < D('0.00001')]
        if len(candidates) > 1 or any(k in used for k in candidates):
            raise ValueError('Ambiguous execution-shortfall residual')
        if candidates:
            key = candidates[0]
            used.add(key)
            matched.append({**record, 'capital_batch': key, 'raw_financing_residual': residuals[key],
                'normalization_rounding_difference': str(D(residuals[key])-D(record['execution_shortfall']))})
        else:
            other.append(record)
    return {'prime': 'grove', 'period': period, 'input_hashes': financing['input_hashes'],
        'scope': 'Observed execution shortfalls only. No cost attribution or allocation sum change; funding may include gains.',
        'raw_unmatched_outflows': len(residuals), 'identified_execution_shortfalls': len(matched),
        'matched_shortfalls_total': str(sum((D(x['execution_shortfall']) for x in matched), D(0))),
        'remaining_after_these_identifications': len(residuals)-len(matched),
        'matched': matched, 'other_observed_shortfalls': other,
        'remaining_residuals': {k:v for k,v in residuals.items() if k not in used}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--financing', type=Path, required=True)
    p.add_argument('--curve-events', type=Path, required=True)
    p.add_argument('--uniswap-events', type=Path)
    p.add_argument('--period', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    financing = json.loads(args.financing.read_text())
    records = curve_shortfalls(json.loads(args.curve_events.read_text()))
    if args.uniswap_events:
        records.extend(uniswap_shortfalls(json.loads(args.uniswap_events.read_text())))
    args.output.write_text(json.dumps(classify(financing, records, args.period), indent=2)+'\n')


if __name__ == '__main__':
    main()
