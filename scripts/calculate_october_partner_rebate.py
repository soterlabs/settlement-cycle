"""Reproduce the approved partner rebate split from retained daily chain values.

No RPC, settlement history rewrite, or change to ordinary investment income.
Method B preserves invoice net amounts; fees are not deducted a second time.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date
from decimal import ROUND_HALF_UP, localcontext
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'reconciliation/2026-10'


def cents(value):
    return format(value.quantize(D('.01'), rounding=ROUND_HALF_UP), '.2f')


def indexed(rows, start, end):
    selected = {r['date']: r for r in rows if start <= r['date'] <= end}
    expected = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if len(selected) != expected or sum(start <= r['date'] <= end for r in rows) != expected:
        raise ValueError('Missing or duplicate daily evidence')
    return selected


def calculate(inputs=None, daily=None):
    inputs = inputs or json.loads((FOLDER / 'partner-rebate-inputs.json').read_text())
    daily = daily or json.loads((FOLDER / 'partner-rebate-daily.json').read_text())
    if inputs['settlement_month'] != '2026-10':
        raise ValueError('Historical rebate is only booked in October 2026')
    receipts = inputs['receipts']
    if len({r['tx'] for r in receipts}) != len(receipts):
        raise ValueError('Duplicate receipt')
    if any(not r['date'].startswith('2026-10-') or r['asset'] not in {'JAAA', 'JTRSY'} for r in receipts):
        raise ValueError('Unexpected receipt month or fund')
    with localcontext() as context:
        context.prec = 60
        gross = {(fund, year): D(0) for fund in ('JAAA', 'JTRSY') for year in ('2025', '2026')}
        sky_gross = dict(gross)
        grove = indexed(daily['grove_daily'], '2025-07-24', '2026-07-31')
        spark = indexed(daily['spark_pretransfer_daily'], '2025-04-07', '2025-07-27')
        for day, row in grove.items():
            year = day[:4]
            rate = D('.001') if day <= '2025-10-30' else D('.002')
            value = D(row['E8_value']) + D(row['E20_value'])
            gross['JAAA', year] += value * rate / 365
            # Approved provisional $325m ETH cap, Nov 20 through Mar 8 inclusive.
            if '2025-11-20' <= day <= '2026-03-08':
                sky_gross['JAAA', year] += min(D(row['E8_value']), D(325000000)) * rate / 365
        jtrsy = {day: D(row['value']) for day, row in spark.items()}
        jtrsy.update({day: D(row['E9_value']) for day, row in grove.items() if day >= '2025-07-28'})
        for day, value in jtrsy.items():
            rate = D('.0025') if '2025-04-23' <= day <= '2025-09-30' else D('.0015')
            gross['JTRSY', day[:4]] += value * rate / 365
        # Historical SDE-like treatment: all JTRSY rebate belongs to Sky,
        # including Spark custody before Grove. Do not apply an Oct 30 cutoff.
        for year in ('2025', '2026'):
            sky_gross['JTRSY', year] = gross['JTRSY', year]
        rows = []
        for fund in ('JAAA', 'JTRSY'):
            total = sum((D(n) for n in inputs['net_rebates'][fund].values()), D(0))
            if sum((D(r['amount']) for r in receipts if r['asset'] == fund), D(0)) != total:
                raise ValueError('Receipt total differs from invoice net: ' + fund)
            for year in ('2025', '2026'):
                net = D(inputs['net_rebates'][fund][year])
                numerator, denominator = sky_gross[fund, year], gross[fund, year]
                if not 0 <= numerator <= denominator or denominator <= 0 or net < 0:
                    raise ValueError('Invalid rebate weight')
                sky = D(cents(net * numerator / denominator))
                rows.append(dict(label='partner rebate', fund=fund, year=year,
                                 onchain_gross=str(denominator), sky_gross=str(numerator),
                                 net=cents(net), sky=cents(sky), grove=cents(net-sky)))
        return dict(status='operator_approved', settlement_month='2026-10', label='partner rebate',
                    method='Invoice net allocated by on-chain gross weights; all JTRSY to Sky',
                    rows=rows, sky={'total':cents(sum(D(r['sky']) for r in rows))},
                    grove={'total':cents(sum(D(r['grove']) for r in rows))},
                    total_received=cents(sum(D(r['amount']) for r in receipts)), receipts=receipts,
                    input_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                  (FOLDER/'partner-rebate-inputs.json', FOLDER/'partner-rebate-daily.json')})


def replacements(historical):
    """Replace ordinary rebate amounts, preserving the independent historical ledger.

    Build from historical-only adjustments, never add a delta to live config.
    This makes repeated runs idempotent and avoids the old $360 statement error.
    """
    rebate = calculate()
    saved = json.loads((FOLDER / 'partner_rebates.json').read_text())
    if saved != rebate:
        raise ValueError('Partner rebate output is stale; regenerate it')
    result = copy.deepcopy(historical['proposed_combined_october_adjustments'])
    for field, owner in [('sky_adj', 'sky'), ('sv_adj', 'grove')]:
        result['grove'][field] = cents(D(historical['adjustment_totals']['grove'][field]) + D(rebate[owner]['total']))
    return result


if __name__ == '__main__':
    result = calculate()
    (FOLDER / 'partner_rebates.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['rows'], indent=2))
