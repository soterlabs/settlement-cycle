#!/usr/bin/env python3
"""Offline marginal CoF replay for issued-but-uncollected deposits.

Retains the existing redemption corrections in the comparison baseline, so
shared days and subsidy-tranche crossings cannot double-count interest relief.
Does not edit settlement configuration or assert payment execution.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reconcile_sde_inflight import FIXTURES, ROOT, frozen_days, reconcile

from settle.compute._helpers import apr_daily, daily_compounding_factor
from settle.compute.sky_revenue import daily_borrow_charge
from settle.normalize.async_vaults import decode_issued_deposit

D = Decimal


def reconcile_deposits() -> dict:
    evidence = json.loads((FIXTURES / 'async_deposits.json').read_text())
    redemptions = reconcile()
    corrected_days = {r['date']: r for r in redemptions['daily']}
    frozen = {month: frozen_days(ROOT / baseline['path'], baseline['sha256'])
              for month, baseline in evidence['baselines'].items()}
    daily, capped_out = [], []
    months = defaultdict(lambda: D(0))
    for row in sorted(evidence['snapshots'], key=lambda r: (r['date'], r['asset'])):
        dep = decode_issued_deposit(row['investment_state'], 6, 6)
        if dep.shares != D(row['max_mint_raw']) / 10**6:
            raise ValueError('Unfiltered issued state disagrees with frozen maxMint')
        nav = D(row['nav_raw']) / 10**18
        value = dep.shares * nav
        if row['asset'] == 'jaaa':
            # No lost deduction: the wallet alone already covers Sky's tranche.
            wallet = D(row['wallet_raw']) / 10**6 * nav
            if wallet < D(325_000_000):
                raise ValueError('JAAA cap no longer covered; replay its historical month')
            capped_out.append({'date': row['date'], 'wallet_value': str(wallet),
                               'issued_deposit_value': str(value), 'cof_change': '0'})
            continue
        day = row['date']
        data, convention = frozen[day[:7]]
        baseline = data[day]
        factor = apr_daily if convention == 'apr' else daily_compounding_factor
        base, sub = factor(baseline['base']), factor(baseline['sub'])
        charge = daily_borrow_charge(baseline['utilized'], base, sub, D(10**9))
        if abs(charge - baseline['charge']) > D('.000001'):
            raise ValueError(f'Cannot reproduce frozen charge: {day}')
        utilized = D(corrected_days[day]['corrected_utilized']) if day in corrected_days else baseline['utilized']
        old = daily_borrow_charge(utilized, base, sub, D(10**9))
        new = daily_borrow_charge(utilized - value, base, sub, D(10**9))
        credit = old - new
        months[day[:7]] += credit
        daily.append({'date': day, 'block': row['block'], 'venue': 'E9',
                      'issued_deposit_value': str(value), 'acquisition_basis': str(dep.capital_usd),
                      'rate_convention': convention, 'baseline_utilized': str(utilized),
                      'corrected_utilized': str(utilized - value), 'old_charge': str(old),
                      'corrected_charge': str(new), 'credit_to_grove': str(credit)})
    total = sum(months.values(), D(0))
    return {'status': 'calculated', 'settlement_month': '2026-10',
            'scope': 'Issued-but-uncollected Grove JTRSY deposits, marginal to existing redemption corrections',
            'note': 'Historical replay, not evidence of payment. Earlier settlement offsets must not be repeated. All identified claims completed within their issuance month; monthly investment income is unchanged.',
            'spark_impact': '0', 'jaaa_cof_impact': '0', 'buidl_deposit_impact': '0',
            'months': {m: str(v) for m, v in sorted(months.items())}, 'daily': daily,
            'capped_out': capped_out, 'baselines': evidence['baselines'],
            'additional_credit_unrounded': str(total),
            'sky_adj': str((-total).quantize(D('.01'), rounding=ROUND_HALF_UP)),
            # Preserve the existing approved line's cent rounding if the two
            # independently rounded corrections are booked together later.
            'combined_redemption_and_deposit_credit': str(
                -D(redemptions['sky_adj']) + total.quantize(D('.01'), rounding=ROUND_HALF_UP))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    payload = json.dumps(reconcile_deposits(), indent=2) + '\n'
    if args.output:
        args.output.write_text(payload)
    else:
        print(payload, end='')


if __name__ == '__main__':
    main()
