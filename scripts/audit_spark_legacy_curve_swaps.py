#!/usr/bin/env python3
"""Read-only witnesses for Spark's legacy and RLUSD Curve swaps."""
from collections import defaultdict
from decimal import Decimal as D

from settle.extract.aave_reconstruct import _words
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.spark_par_swap_evidence import CURVESWAP, HOLDER

USDC = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
USDT = '0xdac17f958d2ee523a2206206994597c13d831ec7'
PYUSD = '0x6c3ea9036406852006290770bedfcaba0e23a0e8'
POOLS = {'0x4f493b7de8aac7d55f71853688b1f7c8f0243c85': (USDC, USDT),
         '0x383e6b4437b59fff47b619cba855ca29342a8559': (PYUSD, USDC)}
RLUSD = '0x8292bb45bf1ee4d140127049757c2e0ff06317ed'
RLUSD_POOLS = {'0xd001ae433f254283fece51d4acce8c53263aa186': (USDC, RLUSD)}
DECIMALS = {USDC: 6, USDT: 6, PYUSD: 6, RLUSD: 18}


def audit(evidence, *, rlusd=False):
    pools = RLUSD_POOLS if rlusd else POOLS
    metadata = evidence['metadata']
    if evidence['holder'] != HOLDER or set(metadata['pools']) != set(pools):
        raise ValueError('Unexpected legacy Curve holder or pool set')
    for pool, coins in pools.items():
        state = metadata['pools'][pool]
        if ([state['coin0'], state['coin1']] != list(coins)
                or state['first_swap_coins'] != list(coins)
                or not 0 < state['first_swap_block'] <= metadata['pin']):
            raise ValueError('Legacy Curve coin controls disagree')
    groups, unique = defaultdict(list), {}
    for row in evidence['rows']:
        key = row['block_number'], row['log_index']
        if key in unique and unique[key] != row:
            raise ValueError('Conflicting legacy Curve log')
        if row['block_number'] > metadata['pin']:
            raise ValueError('Legacy Curve log exceeds pin')
        unique[key] = row
    for row in unique.values():
        groups[row['transaction_hash']].append(row)
    verified, excluded = [], []
    for tx, rows in groups.items():
        if len({(r['block_number'], r['block_time']) for r in rows}) != 1:
            raise ValueError('Conflicting legacy Curve transaction metadata')
        expected, actual = defaultdict(int), defaultdict(int)
        indexes, reason = [], None
        for row in rows:
            pool = row['address']
            if pool in pools and row['topic0'] == CURVESWAP:
                if '0x' + row['topic1'][-40:] != HOLDER:
                    reason = 'other trader'
                    break
                values = _words(row['data'])
                if len(values) != 4:
                    raise ValueError('Invalid legacy Curve exchange layout')
                i, sold, j, bought = values
                if i not in (0, 1) or j != 1-i or min(sold, bought) <= 0:
                    raise ValueError('Invalid legacy Curve exchange amounts')
                if row['block_number'] < metadata['pools'][pool]['first_swap_block']:
                    raise ValueError('Legacy Curve exchange predates coin control')
                expected[(pool, pools[pool][i])] -= sold
                expected[(pool, pools[pool][j])] += bought
                indexes.append(row['log_index'])
            if row['topic0'] == TRANSFER_TOPIC0 and pool in DECIMALS:
                if len(row['data']) != 66:
                    raise ValueError('Invalid legacy Curve cash transfer')
                source, target = '0x'+row['topic1'][-40:], '0x'+row['topic2'][-40:]
                amount = int(row['data'], 16)
                if source == HOLDER and target in pools:
                    actual[(target, pool)] -= amount
                if target == HOLDER and source in pools:
                    actual[(source, pool)] += amount
        expected = {k: v for k, v in expected.items() if v}
        actual = {k: v for k, v in actual.items() if v}
        if reason or not indexes or expected != actual:
            excluded.append({'identity': 'ethereum:'+tx,
                             'reason': reason or ('cash mismatch' if indexes else 'no exchange')})
            continue
        # Use each stablecoin's actual decimals and the tracer's par
        # valuation. These are execution differences, not a
        # claim that all of the difference is the pool's explicit fee.
        gain = sum((D(value) / 10**DECIMALS[token]
                    for (_, token), value in expected.items()), D(0))
        verified.append({'identity': 'ethereum:'+tx, 'block': rows[0]['block_number'],
                         'timestamp': rows[0]['block_time'], 'gain': str(gain),
                         'pools': sorted({p for p, _ in expected}), 'exchange_logs': sorted(indexes)})
    return verified, excluded
