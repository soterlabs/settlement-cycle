#!/usr/bin/env python3
"""Replay the September Ethereum/Base transfer audit from pinned public-chain evidence.

Read-only: prints findings; never writes config or settlement artifacts.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / 'src'))

from settle.domain.config import load_prime_by_id  # noqa: E402
from settle.extract.uniswap_v3 import (  # noqa: E402
    TOPIC_COLLECT,
    TOPIC_DECREASE_LIQUIDITY,
    TOPIC_INCREASE_LIQUIDITY,
)
from settle.normalize.positions import _uniswap_v3_value  # noqa: E402
from settle.normalize.sources.uniswap_v3 import RPCUniswapV3PositionSource  # noqa: E402

EVIDENCE = _REPO / 'docs/grove/evidence/september-2026-venue-audit.json'


def audit(evidence: dict) -> dict:
    prime = load_prime_by_id('grove', _REPO / 'config')
    venues = {v.id: v for v in prime.venues}
    calls = {(r['chain'], r['method'], json.dumps(r['params'], sort_keys=True)): r['result']
             for r in evidence['rpc_calls']}

    def rpc(_url, method, params):
        # No network fallback: missing pinned evidence must fail visibly.
        return calls['ethereum', method, json.dumps(params, sort_keys=True)]

    inventory = []
    for chain, holders in evidence['holders'].items():
        opening, closing = evidence['blocks'][chain]
        unique = {}
        for r in evidence['transfers'][chain]:
            if not opening < int(r['blockNumber'], 16) <= closing:
                raise ValueError('Transfer outside September boundary')
            key = r['transactionHash'], r['logIndex']
            if key in unique:
                raise ValueError('Duplicate transfer identity')
            unique[key] = r
        for name, holder in holders.items():
            rows = [r for r in unique.values()
                    if holder in ['0x' + t[-40:] for t in r['topics'][1:3]]]
            inventory.append({'chain': chain, 'holder': name, 'logs': len(rows),
                              'transactions': len({r['transactionHash'] for r in rows})})

    vaults = {}
    holder = evidence['holders']['base']['legacy']
    for vid in ['E19', 'E23', 'E43']:
        vault = venues[vid].token.address.hex
        state = evidence['vault_states'][vid]
        opening = D(state['start']['balance']) * D(state['start']['nav']) / 10**24
        closing = D(state['end']['balance']) * D(state['end']['nav']) / 10**24
        daily = defaultdict(int)
        share_delta = 0
        for r in evidence['transfers']['base']:
            if r['address'] != vault:
                continue
            sign = int('0x' + r['topics'][2][-40:] == holder) - int('0x' + r['topics'][1][-40:] == holder)
            amount = sign * int(r['data'], 16)
            daily[evidence['base_block_dates'][str(int(r['blockNumber'], 16))]] += amount
            share_delta += amount
        if int(state['end']['balance']) - int(state['start']['balance']) != share_delta:
            raise ValueError(f'{vid}: transfer scan does not explain share balance change')
        # Existing Cat B convention: each day's net shares priced at that day's EOD NAV.
        capital = sum((D(amount) * D(evidence['daily_nav'][vault][day]) / 10**24
                       for day, amount in daily.items()), D(0))
        cash = D(0)
        for r in evidence['transfers']['base']:
            if r['address'] != venues[vid].underlying.address.hex:
                continue
            sender, recipient = ['0x' + t[-40:] for t in r['topics'][1:3]]
            cash += D(int(r['data'], 16)) / 10**6 * (
                int((sender, recipient) == (holder, vault)) - int((sender, recipient) == (vault, holder)))
        vaults[vid] = {'opening': str(opening), 'closing': str(closing),
                       'capital_at_daily_nav': str(capital), 'cash_deposited_net': str(cash),
                       'revenue_existing_method': str(closing - opening - capital),
                       'return_vs_cash': str(closing - opening - cash)}

    lp = {}
    # Reuse production V3 valuation, including pending fees. The archive contains
    # all eth_call inputs/outputs, so the replay needs neither RPC keys nor internet.
    with tempfile.TemporaryDirectory() as cache, patch.dict(os.environ, {
        'SETTLE_CACHE_DIR': cache, 'ETH_RPC': 'https://offline.invalid',
    }), patch('settle.extract.rpc._post', rpc):
        source = RPCUniswapV3PositionSource()
        for vid in ['E12', 'E44']:
            venue = venues[vid]
            start, end = evidence['blocks']['ethereum']
            values = [_uniswap_v3_value(prime, venue, b, source=source) for b in [start, end]]
            owner = (venue.holder_override or prime.alm[venue.chain]).value
            ids = {p.token_id for b in [start, end] for p in source.positions_in_pool(
                chain='ethereum', owner=owner, pool=venue.token.address.value, block=b)}
            capital = fees = 0
            owed = defaultdict(lambda: [0, 0])
            for r in sorted(evidence['nfpm_logs'], key=lambda x: (int(x['blockNumber'], 16), int(x['logIndex'], 16))):
                if r['topics'][0] not in [TOPIC_COLLECT, TOPIC_INCREASE_LIQUIDITY, TOPIC_DECREASE_LIQUIDITY]:
                    continue
                tid = int(r['topics'][1], 16)
                if tid not in ids:
                    continue
                data = r['data'][2:]
                amounts = [int(data[64:128], 16), int(data[128:192], 16)]
                if r['topics'][0] == TOPIC_COLLECT:
                    for i, amount in enumerate(amounts):
                        fees += max(0, amount - owed[tid][i])
                        owed[tid][i] -= min(amount, owed[tid][i])
                else:
                    increase = r['topics'][0] == TOPIC_INCREASE_LIQUIDITY
                    capital += sum(amounts) * (1 if increase else -1)
                    if not increase:
                        for i, amount in enumerate(amounts):
                            owed[tid][i] += amount
            inflow = D(capital - fees) / 10**6
            lp[vid] = {'opening': str(values[0]), 'closing': str(values[1]),
                       'token_ids': sorted(ids), 'capital_net_of_collections': str(inflow),
                       'fee_collections': str(D(fees) / 10**6),
                       'revenue': str(values[1] - values[0] - inflow)}
    return {'inventory': inventory, 'vaults': vaults, 'uniswap': lp}


if __name__ == '__main__':
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else EVIDENCE
    print(json.dumps(audit(json.loads(path.read_text())), indent=2))
