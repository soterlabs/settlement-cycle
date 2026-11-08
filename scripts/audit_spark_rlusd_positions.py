#!/usr/bin/env python3
"""Complete the saved RLUSD position audit with withdrawals and share controls."""
import argparse
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from types import SimpleNamespace

from audit_saved_funding_gap import read
from audit_spark_rlusd_routes import ALM, RLUSD, SENTORA, SPRLUSD, TRANSFER, ZERO, address

from settle.extract.aave_reconstruct import BT_T0, BURN_T0, MINT_T0, ray_mul, scaled_balance_at

WITHDRAW = '0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db'
OPEN, CLOSE = 25878704, 26093737


def words(row):
    h = row['data'][2:]
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def match_withdrawal(rows, target, cash):
    """Return evidence for cash redeemed, including Mint-on-withdraw aToken events."""
    events = []
    amount = 0
    for r in rows:
        if r['address'] != target:
            continue
        w = words(r)
        if target == SENTORA and r['topic0'] == WITHDRAW and address(r['topic2']) == ALM and address(r['topic3']) == ALM:
            amount += w[0]
            events.append({'kind': 'Withdraw', 'cash_raw': w[0], 'shares_burned_raw': w[1]})
        elif target == SPRLUSD and r['topic0'] == BURN_T0 and address(r['topic1']) == ALM and address(r['topic2']) == ALM:
            amount += w[0] + w[1]
            events.append({'kind': 'Burn', 'cash_raw': w[0] + w[1], 'balance_increase_raw': w[1], 'index': w[2]})
        elif target == SPRLUSD and r['topic0'] == MINT_T0 and address(r['topic2']) == ALM and w[1] > w[0]:
            amount += w[1] - w[0]
            events.append({'kind': 'Mint_on_withdrawal', 'cash_raw': w[1] - w[0], 'balance_increase_raw': w[1], 'index': w[2]})
    if amount != cash or not events:
        raise ValueError('Unmatched cash withdrawal')
    if target == SENTORA:
        burns = sum(int(r['data'], 16) for r in rows if r['address'] == target and r['topic0'] == TRANSFER
                    and address(r['topic1']) == ALM and address(r['topic2']) == ZERO)
        if burns != sum(e['shares_burned_raw'] for e in events):
            raise ValueError('Withdraw shares do not match burns')
    return events


def analyze(logs, calls, positions):
    with localcontext() as ctx:
        ctx.prec = 60
        unique = {}
        for r in logs:
            if not OPEN < r['block_number'] <= CLOSE:
                raise ValueError('Log outside September pins')
            key = (r['block_number'], r['log_index'])
            if key in unique and unique[key] != r:
                raise ValueError('Conflicting duplicate log')
            unique[key] = r
        txs = defaultdict(list)
        for r in unique.values():
            txs[r['transaction_hash']].append(r)
        withdrawals = []
        transfers = []
        net_shares = 0
        for tx, rows in txs.items():
            for target in (SENTORA, SPRLUSD):
                cash = sum(int(r['data'], 16) for r in rows if r['address'] == RLUSD and r['topic0'] == TRANSFER
                           and address(r['topic1']) == target and address(r['topic2']) == ALM)
                if cash:
                    withdrawals.append({'tx': tx, 'venue': target, 'date': str(datetime.fromtimestamp(rows[0]['block_time'], UTC).date()),
                        'cash': D(cash)/D(10**18), 'events': match_withdrawal(rows, target, cash)})
            for r in rows:
                if r['address'] not in (SENTORA, SPRLUSD) or r['topic0'] != TRANSFER:
                    continue
                a, b, value = address(r['topic1']), address(r['topic2']), int(r['data'], 16)
                if r['address'] == SENTORA:
                    net_shares += value * ((b == ALM) - (a == ALM))
                if ALM in (a, b) and ZERO not in (a, b):
                    transfers.append({'tx': tx, 'token': r['address'], 'from': a, 'to': b, 'amount': D(value)/D(10**18)})
        if positions['sentora_opening_code'] != '0x':
            raise ValueError('Sentora zero opening requires proof of no deployed code')
        if net_shares != int(calls[f'{SENTORA}:balanceOf(address):{CLOSE}']['result'], 16):
            raise ValueError('Sentora closing shares mismatch')
        if int(positions[f'{SPRLUSD}:scaledBalanceOf(address):{OPEN}'], 16) != 0:
            raise ValueError('Nonzero spRLUSD opening requires earlier event history')
        relevant = [SimpleNamespace(**r) for r in unique.values() if r['address'] == SPRLUSD and (
            (r['topic0'] == MINT_T0 and address(r['topic2']) == ALM)
            or (r['topic0'] == BURN_T0 and address(r['topic1']) == ALM)
            or (r['topic0'] == BT_T0 and ALM in (address(r['topic1']), address(r['topic2']))))]
        scaled = scaled_balance_at('ethereum', bytes.fromhex(SPRLUSD[2:]), bytes.fromhex(ALM[2:]), CLOSE,
                                   fetch_logs=lambda *args: relevant)
        if scaled != int(positions[f'{SPRLUSD}:scaledBalanceOf(address):{CLOSE}'], 16):
            raise ValueError('spRLUSD closing scaled balance mismatch')
        pool = address(positions[f'{SPRLUSD}:POOL():{CLOSE}'])
        nominal = ray_mul(scaled, int(positions[f'{pool}:getReserveNormalizedIncome(address):{CLOSE}'], 16))
        if nominal != int(calls[f'{SPRLUSD}:balanceOf(address):{CLOSE}']['result'], 16):
            raise ValueError('spRLUSD closing balance mismatch')
        return {'publishable': False, 'scope': 'Position cash and share controls, not funding reattribution.',
                'withdrawals': withdrawals, 'withdrawal_total': sum((r['cash'] for r in withdrawals), D(0)),
                'receipt_transfers': transfers, 'sentora_closing_shares': D(net_shares)/D(10**18),
                'sprlusd_closing_scaled_raw': scaled, 'sprlusd_closing_balance': D(nominal)/D(10**18),
                'share_control_differences_raw': {'sentora': 0, 'sprlusd_scaled': 0, 'sprlusd_nominal': 0}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = [args.evidence_dir / n for n in ('receipt-token-joined-logs.json.gz', 'boundary-calls.json', 'position-calls.json')]
    result = analyze(*(read(p) for p in paths))
    result['input_sha256'] = {}
    for p in paths:
        with p.open('rb') as f:
            result['input_sha256'][str(p)] = hashlib.file_digest(f, 'sha256').hexdigest()
    args.output.write_text(json.dumps(result, default=str, indent=2) + '\n')
    print('Withdrawals:', result['withdrawal_total'], 'Exact share controls:', result['share_control_differences_raw'])


if __name__ == '__main__':
    main()
