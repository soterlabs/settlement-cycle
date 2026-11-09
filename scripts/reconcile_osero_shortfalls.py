#!/usr/bin/env python3
"""Reproduce Osero's duplicate-deduction refund from frozen reports and receipts.

Read-only unless --output is supplied. Does not regenerate historical reports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import load_workbook

from settle.normalize.sources.hypersync_debt import _decode_dart

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = 'reconciliation/2026-10/osero_shortfall_evidence.json'
VAT = '0x35d1b3f3d7966a1dfe207aa4514c12a259a0492b'
USDS = '0xdc035d45d973e3ec169d2276ddab16f1e407384f'
ILK = '0x414c4c4f4341544f522d505259534d2d41000000000000000000000000000000'
SUBPROXY = '24fdcd3bfa5c2553e05b2f9ad0365ebc296278d3'
TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
ZERO = '0x' + '0' * 64


def whole(value: Decimal) -> Decimal:
    return value.quantize(Decimal(1), rounding=ROUND_HALF_UP)


def components(path: Path) -> tuple[Decimal, Decimal, Decimal]:
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        rows = {str(r[0]): Decimal(str(r[1])) for r in workbook['Summary'].values
                if r[0] in {
                    'prime_agent_revenue (gross venue yield to prime)',
                    '+ agent_rate (subproxy USDS / sUSDS yield)',
                    '+ distribution_rewards (settle-dr-dune)',
                    'CoF on utilized (BR × Net_Subs)',  # noqa: RUF001 — exact workbook label
                    '+ SDE revenue (Sky-Direct, full to Sky)',
                }}
    finally:
        workbook.close()
    revenue = rows['prime_agent_revenue (gross venue yield to prime)']
    demand = (rows['+ agent_rate (subproxy USDS / sUSDS yield)']
              + rows['+ distribution_rewards (settle-dr-dune)'])
    cof = rows['CoF on utilized (BR × Net_Subs)']  # noqa: RUF001 — exact workbook label
    if rows['+ SDE revenue (Sky-Direct, full to Sky)'] != 0:
        raise ValueError('This refund replay expects no Osero SDE revenue')
    if any(not v.is_finite() for v in rows.values()):
        raise ValueError('Non-finite workbook component')
    return revenue, demand, cof


def reconcile(root: Path = ROOT) -> dict:
    evidence = json.loads((root / EVIDENCE).read_text())
    months = [row['earning_month'] for row in evidence['rows']]
    if months != ['2026-07', '2026-08', '2026-09']:
        raise ValueError('Expected exactly the three published Osero earning cycles')
    rows = []
    hashes = set()
    for item in evidence['rows']:
        path = root / item['workbook']
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['workbook_sha256']:
            raise ValueError(f'Historical workbook changed: {path}')
        revenue, demand, cof = components(path)
        supply = revenue - cof
        if supply >= 0:
            raise ValueError('Expected a negative historical supply share')
        receipt = item['receipt']
        tx = receipt['transactionHash']
        if receipt['status'] != '0x1' or tx in hashes:
            raise ValueError('Failed or duplicated settlement transaction')
        hashes.add(tx)
        for log in item['logs']:
            if (log['transactionHash'] != tx or log['blockNumber'] != receipt['blockNumber']
                    or log['blockHash'] != receipt['blockHash'] or log.get('removed')):
                raise ValueError('Receipt log identity mismatch')
        grabs = [log for log in item['logs'] if log['address'].lower() == VAT
                 and log['topics'][0] == '0x7bab3f40' + '0' * 56
                 and log['topics'][1] == ILK]
        payments = [log for log in item['logs'] if log['address'].lower() == USDS
                    and log['topics'] == [TRANSFER, ZERO, '0x' + '0' * 24 + SUBPROXY]]
        if len(grabs) != 1 or len(payments) != 1:
            raise ValueError('Expected one Osero debt grab and one subproxy payment')
        call = item['ilk_rate_call']
        if (call['to'].lower() != VAT or call['data'] != '0xd9638d36' + ILK[2:]
                or call['block'] != receipt['blockNumber'] or len(call['result']) != 322):
            raise ValueError('Invalid settlement-block ilk-rate witness')
        rate = int(call['result'][66:130], 16)
        dart = _decode_dart(grabs[0]['data'])
        mint = Decimal(dart) * Decimal(rate) / Decimal(10) ** 45
        paid = Decimal(int(payments[0]['data'], 16)) / Decimal(10) ** 18
        if dart <= 0 or mint != whole(cof) or paid != whole(demand + supply):
            raise ValueError('Executed cashflows do not match the duplicate-deduction formula')
        corrected = whole(demand)
        rows.append({
            'earning_month': item['earning_month'], 'executed_at': item['executed_at'],
            'transaction_hash': tx, 'demand_revenue': str(demand),
            'supply_share': str(supply), 'cof': str(cof), 'debt_minted': str(mint),
            'actual_send': str(paid), 'corrected_send': str(corrected),
            'send_credit': str(corrected - paid),
        })
    return {
        'prime': 'osero', 'settlement_earning_cycle': '2026-10',
        'method': 'Sum corrected rounded demand payments less executed payments; no debt increase.',
        'evidence': EVIDENCE, 'rows': rows,
        'send_credit': str(sum((Decimal(r['send_credit']) for r in rows), Decimal(0))),
        'mint_adjustment': '0',
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    text = json.dumps(reconcile(), indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
