#!/usr/bin/env python3
"""Replay identified in-flight CoF corrections against immutable published rates.

Offline and read-only by default. Does not regenerate settlements or book a
payment. Includes known negative corrections and deducts prior recorded credits.
Run: python scripts/reconcile_sde_inflight.py [--output reconciliation/...json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import load_workbook

from settle.compute._helpers import apr_daily, daily_compounding_factor
from settle.compute.sky_revenue import daily_borrow_charge
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month, Period
from settle.normalize.redemption_settlements import match_redemptions, policy_for

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/sde_inflight'
D = Decimal


def frozen_days(path: Path, expected_hash: str) -> tuple[dict, str]:
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError(f'Frozen workbook changed: {path}; review the reconciliation baseline')
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(wb['Debt'].values)
        header = rows[4]
        base_index = next(i for i, value in enumerate(header) if value in {'base APR', 'base APY'})
        sub_index = next(i for i, value in enumerate(header) if value in {'sub APR', 'sub APY'})
        charge_index = header.index('daily Sky charge')
        utilized_index = header.index('SSR APY') - 1
        convention = 'apr' if 'APR' in header[base_index] else 'apy'
        days = {}
        for row in rows[5:]:
            if isinstance(row[0], str) and row[0].startswith('2026-'):
                days[row[0]] = {'utilized': D(str(row[utilized_index])), 'base': D(str(row[base_index])),
                                'sub': D(str(row[sub_index])), 'charge': D(str(row[charge_index]))}
        return days, convention
    finally:
        wb.close()


def reconcile() -> dict:
    evidence = json.loads((FIXTURES / 'centrifuge_history.json').read_text())
    buidl_events = json.loads((FIXTURES / 'buidl_events.json').read_text())['events']
    prime = load_prime_by_id('grove', config_dir=ROOT / 'config')
    venue = next(v for v in prime.venues if v.id == 'E10')
    policy = policy_for('grove', 'E10')
    policy['history_start'] = policy['cof_history_start']
    snapshots = {(r['asset'], r['date']): r for r in evidence['snapshots']}
    deltas: dict[str, dict[str, Decimal]] = defaultdict(dict)
    # Only Apr/May predate the automatic Centrifuge fix. August's JTRSY
    # receivable was ALREADY in the published baseline; adding it again is wrong.
    for (asset, day), r in snapshots.items():
        if asset == 'jtrsy' and day[:7] in {'2026-04', '2026-05'}:
            nav = D(r['nav_precise_raw']) / D(10**18)
            deltas[day]['E9'] = (D(r['pending_raw']) + D(r['claimable_raw'])) / D(10**6) * nav
        if asset == 'jaaa':
            # Every January gap still leaves >$325M held: no lost CoF deduction.
            if D(r['balance_raw']) / D(10**6) * D(r['nav_raw']) / D(10**6) < D('325000000'):
                raise ValueError('JAAA January cap coverage changed; replay required')
    # The old capped-tranche rule retained the entire deduction on cash day.
    # This is a debit to Grove, not another missed-interest credit.
    deltas['2026-03-11']['E8'] = D('-325000000')

    months, daily = {}, []
    cap = prime.subsidy.cap_usd
    for label, baseline in evidence['baselines'].items():
        year, month = map(int, label.split('-'))
        period = Period.from_month(Month(year, month), {venue.chain: baseline['pin']})
        ledger = match_redemptions(venue, period, policy, buidl_events, track_pending=True)
        for row in ledger.pending_daily:
            if row['pending_value_usd']:
                deltas[str(row['block_date'])]['E10'] = row['pending_value_usd']
        days, convention = frozen_days(ROOT / baseline['path'], baseline['sha256'])
        gross, max_baseline_error = D(0), D(0)
        for day, row in days.items():
            factor = apr_daily if convention == 'apr' else daily_compounding_factor
            base_factor, sub_factor = factor(row['base']), factor(row['sub'])

            original = daily_borrow_charge(row['utilized'], base_factor, sub_factor, cap)
            error = abs(original - row['charge'])
            max_baseline_error = max(max_baseline_error, error)
            if error > D('0.000001'):
                raise ValueError(f'Cannot reproduce frozen daily charge at {day}: {error}')
            if day not in deltas:
                continue
            delta = sum(deltas[day].values(), D(0))
            corrected = daily_borrow_charge(row['utilized'] - delta, base_factor, sub_factor, cap)
            credit = original - corrected
            gross += credit
            daily.append({'date': day, 'rate_convention': convention,
                          'deduction_changes_by_venue': {k: str(v) for k, v in deltas[day].items()},
                          'frozen_utilized': str(row['utilized']), 'corrected_utilized': str(row['utilized'] - delta),
                          'frozen_charge': str(original), 'corrected_charge': str(corrected),
                          'credit_to_grove': str(credit)})
        prior = D(evidence['already_recorded_cof_credit'].get(label, '0'))
        months[label] = {'gross_credit': str(gross), 'already_recorded_credit': str(prior),
                         'additional_credit': str(gross - prior),
                         'baseline_max_daily_error': str(max_baseline_error), **baseline}
    total = sum((D(row['additional_credit']) for row in months.values()), D(0))
    return {'status': 'proposal_for_review_not_booked', 'baseline_commit': evidence['source']['baseline_commit'],
            'scope': 'Grove Ethereum E8/E9/E10, identified March-September 2026 CoF timing corrections only',
            'note': 'Positive credit reduces Sky share/debt mint. Negative credit increases it. Published reports remain unchanged. Confirm executed/off-report credits before booking.',
            'months': months, 'daily': sorted(daily, key=lambda r: r['date']),
            'additional_credit_unrounded': str(total),
            'proposed_sky_adj': str((-total).quantize(D('0.01'), rounding=ROUND_HALF_UP))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = reconcile()
    payload = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    else:
        print(payload, end='')


if __name__ == '__main__':
    main()
