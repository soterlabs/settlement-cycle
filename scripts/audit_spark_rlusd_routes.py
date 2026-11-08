#!/usr/bin/env python3
"""Audit saved September RLUSD transfer/receipt evidence, without replay or writes to the API."""
import argparse
import hashlib
import json
from collections import defaultdict
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_saved_funding_gap import decompose, read

ALM = '0x1601843c5e9bc251a3272907010afa41fa18347e'
RLUSD = '0x8292bb45bf1ee4d140127049757c2e0ff06317ed'
SENTORA = '0xfc8c624b6080a0a780583799f2a862de936f6e22'
SPRLUSD = '0x59275fb72c8004f44ba44432e25082932fd677f1'
TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
DEPOSIT = '0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7'
ZERO = '0x' + '0' * 40


def address(topic):
    return '0x' + topic[-40:]


def analyze(logs, balances, residual_accounts):
    """Match missing receipt-token positions; retain associated costs as provisional."""
    with localcontext() as ctx:
        ctx.prec = 60
        unique = {}
        for row in logs:
            key = row['block_number'], row['log_index']
            if key in unique and unique[key] != row:
                raise ValueError('Conflicting duplicate log')
            unique[key] = row
        txs = defaultdict(list)
        counterparties = defaultdict(lambda: {'in': D(0), 'out': D(0), 'in_logs': 0, 'out_logs': 0})
        for row in unique.values():
            txs[row['transaction_hash']].append(row)
            if row['address'] == RLUSD and row['topic0'] == TRANSFER:
                sender, receiver = address(row['topic1']), address(row['topic2'])
                amount = D(int(row['data'], 16)) / D(10**18)
                for party, direction in ((receiver, 'out'), (sender, 'in')):
                    if (direction == 'out' and sender == ALM) or (direction == 'in' and receiver == ALM):
                        counterparties[party][direction] += amount
                        counterparties[party][direction + '_logs'] += 1
        net = sum((r['in'] - r['out'] for r in counterparties.values()), D(0))
        if D(balances['closing']) - D(balances['opening']) != net:
            raise ValueError('RLUSD transfers do not reconcile pinned balances')
        routes = {}
        costs = {a['account'].removeprefix('unallocated:ethereum:'): a['cost'] for a in residual_accounts
                 if a['account'].startswith('unallocated:ethereum:')}
        attributed = set()
        for target in (SENTORA, SPRLUSD):
            matched, failed = [], []
            total_cost = D(0)
            for tx, rows in txs.items():
                cash = sum(int(r['data'], 16) for r in rows if r['address'] == RLUSD
                    and r['topic0'] == TRANSFER and address(r['topic1']) == ALM
                    and address(r['topic2']) == target)
                if not cash:
                    continue
                minted = sum(int(r['data'], 16) for r in rows if r['address'] == target
                    and r['topic0'] == TRANSFER and address(r['topic1']) == ZERO
                    and address(r['topic2']) == ALM)
                deposits = [r for r in rows if r['address'] == target and r['topic0'] == DEPOSIT
                            and address(r['topic2']) == ALM]
                assets = sum(int(r['data'][2:66], 16) for r in deposits)
                shares = sum(int(r['data'][66:130], 16) for r in deposits)
                verified = minted > 0 and (target != SENTORA or (assets == cash and shares == minted))
                if not verified:
                    failed.append(tx)
                    continue
                cost = D(costs.get(tx, 0))
                # Mixed destinations need explicit splitting, never duplicate the entire residual.
                if tx in attributed and cost:
                    raise ValueError('Multiple missing destinations share one residual')
                attributed.add(tx)
                total_cost += cost
                matched.append({'tx': tx, 'block': rows[0]['block_number'],
                    'cash': D(cash)/D(10**18), 'shares_minted': D(minted)/D(10**18), 'associated_cost': cost})
            routes[target] = {'matched_transactions': len(matched), 'failed_matches': failed,
                'associated_residual_cost': total_cost, 'examples': sorted(matched, key=lambda r: r['cash'], reverse=True)[:5]}
        return {'publishable': False, 'scope': 'Observed routes and associated old-replay costs; not recalculated venue costs.',
            'log_count': len(unique), 'transaction_count': len(txs), 'rlusd_balance_change': net,
            'balance_control': balances, 'counterparties': dict(counterparties), 'routes': routes,
            'associated_residual_cost': sum((r['associated_residual_cost'] for r in routes.values()), D(0))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--saved-dir', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    evidence, saved = args.evidence_dir, args.saved_dir
    paths = [args.control, *(saved / n for n in ('funding-snapshot.json.gz', 'conditional-costs.json.gz',
        'daily-by-ilk.json.gz', 'vat-accrual.json'))]
    control = read(args.control)
    report = decompose(*(read(p) for p in paths))
    logs_path = evidence / 'september-rlusd-joined-logs.json.gz'
    calls_path = evidence / 'boundary-calls.json'
    calls = read(calls_path)
    opening, closing = control['pin_blocks_som']['ethereum'], control['pin_blocks_eom']['ethereum']
    with localcontext() as ctx:
        ctx.prec = 60
        balances = {label: str(D(int(calls[f'{RLUSD}:balanceOf(address):{block}']['result'], 16))/D(10**18))
                    for label, block in (('opening', opening), ('closing', closing))}
    logs = read(logs_path)
    if any(not opening < r['block_number'] <= closing for r in logs):
        raise ValueError('Evidence contains logs outside pinned period')
    for token, method in ((SENTORA, 'asset()'), (SPRLUSD, 'UNDERLYING_ASSET_ADDRESS()')):
        if int(calls[f'{token}:{method}:{closing}']['result'], 16) != int(RLUSD, 16):
            raise ValueError('Unexpected underlying asset')
    result = analyze(logs, balances, report['accounts'])
    result['input_sha256'] = {}
    for path in [*paths, logs_path, calls_path]:
        with path.open('rb') as stream:
            result['input_sha256'][str(path)] = hashlib.file_digest(stream, 'sha256').hexdigest()
    args.output.write_text(json.dumps(result, default=str, indent=2) + '\n')
    print(result['associated_residual_cost'])


if __name__ == '__main__':
    main()
