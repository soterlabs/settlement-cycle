#!/usr/bin/env python3
"""Replay the September Ethereum/Base transfer audit from pinned public-chain evidence.

Read-only: prints findings; never writes config or settlement artifacts.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / 'src'))

from settle.compute.prime_agent_revenue import _time_weighted_notional  # noqa: E402
from settle.domain import Month, Period  # noqa: E402
from settle.domain.config import load_prime_by_id  # noqa: E402
from settle.extract.transfer_logs import TRANSFER_TOPIC0  # noqa: E402
from settle.extract.uniswap_v3 import (  # noqa: E402
    TOPIC_COLLECT,
    TOPIC_DECREASE_LIQUIDITY,
    TOPIC_INCREASE_LIQUIDITY,
)
from settle.normalize.positions import _uniswap_v3_value  # noqa: E402
from settle.normalize.sources.uniswap_v3 import RPCUniswapV3PositionSource  # noqa: E402

EVIDENCE = _REPO / 'docs/grove/evidence/september-2026-venue-audit.json'


def validate_evidence(evidence: dict, prime) -> dict:
    """Reject incomplete or mis-scoped inputs before calculating any amounts.

    Counts are collection metadata, not a proof of on-chain completeness. The
    scan's scope and receipt-discovery limitations still apply.
    """
    if evidence['period'] != '2026-09' or evidence['blocks'] != {
        'ethereum': [25878704, 26093737], 'base': [50715726, 52011726],
    }:
        raise ValueError('Evidence must use the verified September boundaries')
    venues = {v.id: v for v in prime.venues}
    expected_holders = {
        'ethereum': {
            'legacy': prime.alm[venues['E12'].chain].hex,
            'diamond': venues['E44'].holder_override.hex,
            'lp_alt': venues['E30'].holder_override.hex,
        },
        'base': {'legacy': prime.alm[venues['E43'].chain].hex},
    }
    if evidence['holders'] != expected_holders:
        raise ValueError('Evidence holders differ from the configured venues')

    def validate_logs(rows, chain, *, nfpm=False):
        opening, closing = evidence['blocks'][chain]
        seen = set()
        for row in rows:
            if not opening < int(row['blockNumber'], 16) <= closing:
                raise ValueError('Event outside September boundary')
            identity = row['transactionHash'], int(row['logIndex'], 16)
            if identity in seen:
                raise ValueError('Duplicate event identity')
            seen.add(identity)
            topics, data = row['topics'], row['data']
            if nfpm:
                if row['address'] != venues['E44'].nft_position_manager.hex:
                    raise ValueError('Unexpected NFPM event emitter')
                valid = (len(topics) == 2 and topics[0] in {
                    TOPIC_COLLECT, TOPIC_INCREASE_LIQUIDITY, TOPIC_DECREASE_LIQUIDITY,
                } and len(data) == 194)
            else:
                valid = (len(topics) in {3, 4} and topics[0] == TRANSFER_TOPIC0
                         and len(data) == (66 if len(topics) == 3 else 2))
            if not valid:
                raise ValueError('Malformed event topics or data')
            if nfpm:
                int(data[2:], 16)

    for chain, rows in evidence['transfers'].items():
        validate_logs(rows, chain)
        coverage = evidence['coverage'][chain]
        opening, closing = evidence['blocks'][chain]
        expected_chunks = (closing - opening) // coverage['chunk_size'] + 1
        if (coverage['completed_chunks'] != expected_chunks
                or coverage['transfer_logs'] != len(rows)):
            raise ValueError('Incomplete transfer evidence')
    validate_logs(evidence['nfpm_logs'], 'ethereum', nfpm=True)
    if len(evidence['nfpm_logs']) != evidence['nfpm_event_count']:
        raise ValueError('Incomplete NFPM evidence')
    calls = {}
    for row in evidence['rpc_calls']:
        key = row['chain'], row['method'], json.dumps(row['params'], sort_keys=True)
        if key in calls and calls[key] != row['result']:
            raise ValueError('Conflicting pinned RPC evidence')
        calls[key] = row['result']
    return calls


def audit(evidence: dict) -> dict:
    prime = load_prime_by_id('grove', _REPO / 'config')
    venues = {v.id: v for v in prime.venues}
    calls = validate_evidence(evidence, prime)

    def rpc(_url, method, params):
        # No network fallback: missing pinned evidence must fail visibly.
        return calls['ethereum', method, json.dumps(params, sort_keys=True)]

    inventory = []
    for chain, holders in evidence['holders'].items():
        for name, holder in holders.items():
            rows = [r for r in evidence['transfers'][chain]
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
    # Bypass BOTH cache layers: a fresh local directory alone still permits
    # Postgres reads and writes when the operator has DATABASE_URL configured.
    with patch.dict(os.environ, {
        'SETTLE_NO_CACHE': '1', 'SETTLE_REQUIRE_POSTGRES': '0',
        'ETH_RPC': 'https://offline.invalid',
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
    warehouse = venues['E42']
    relays = {a.hex for a in warehouse.notional_counterparties}
    legacy = evidence['holders']['ethereum']['legacy']
    funding = defaultdict(D)
    transactions = []
    for row in evidence['transfers']['ethereum']:
        if row['address'] != warehouse.token.address.hex:
            continue
        sender, recipient = ['0x' + topic[-40:] for topic in row['topics'][1:3]]
        sign = int(sender == legacy and recipient in relays) - int(recipient == legacy and sender in relays)
        if not sign:
            continue
        block = evidence['ethereum_block_dates'][str(int(row['blockNumber'], 16))]
        day = datetime.fromtimestamp(block['timestamp'], UTC).date()
        if block['block_hash'] != row['blockHash'] or day.isoformat() != block['date']:
            raise ValueError('Warehouse transaction date is not pinned to its block')
        amount = sign * D(int(row['data'], 16)) / D(10**warehouse.token.decimals)
        funding[day] += amount
        transactions.append({'date': str(day), 'net_funding': str(amount),
                             'tx': row['transactionHash'], 'log_index': int(row['logIndex'], 16)})
    period = Period.from_month(Month(2026, 9))
    schedule = warehouse.notional_principal_usd

    def principal(day):
        return next((entry.amount for entry in reversed(schedule) if entry.start_date <= day), D(0))

    previous = principal(period.start - timedelta(days=1))
    opening = previous
    for i in range(period.n_days):
        day = period.start + timedelta(days=i)
        current = principal(day)
        if current - previous != funding[day]:
            raise ValueError(f'Warehouse schedule does not match net funding on {day}')
        previous = current
    return {'inventory': inventory, 'vaults': vaults, 'uniswap': lp,
            'warehouse': {'opening': str(opening), 'closing': str(previous),
                          'average_principal': str(_time_weighted_notional(schedule, period)),
                          'net_funding': str(sum(funding.values(), D(0))),
                          'transactions': transactions}}


if __name__ == '__main__':
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else EVIDENCE
    print(json.dumps(audit(json.loads(path.read_text())), indent=2))
