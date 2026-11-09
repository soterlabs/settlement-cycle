#!/usr/bin/env python3
"""Replay pre-September BUIDL cash costs, prior fee credits and omitted share capital.

Read-only unless --output is supplied. Uses the existing redemption matcher;
never moves the production September recognition date or books another CoF credit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from pathlib import Path

from settle.domain import Chain, Period
from settle.domain.config import load_prime_by_id
from settle.normalize.redemption_settlements import match_redemptions, policy_for

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / 'tests/fixtures/sde_inflight/buidl_events.json'
PRIOR = ROOT / 'reconciliation/2026-01_to_2026-08/data.json'
PRIOR_SHA256 = '8012e9c6ceb5f8bfcadc51d71ca4aa16676c78e23a92541e4cecf974db031357'
START, END = date(2026, 1, 1), date(2026, 8, 31)
CLOSING_BLOCK = 25878704
BASELINES = {
    '2026-05': '62a4da6af6cd0015c894b681e62029afe6256bef25554e60e1c5aca3ad8e1060',
    '2026-08': 'ee15f0ffa0f84333eeb5b6aeadf7fd3de3174767848661afe40692ace705c14d',
}


def published_baseline(month: str) -> dict:
    path = ROOT / 'settlements' / 'grove' / month / 'summary.md'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != BASELINES[month]:
        raise ValueError('Published BUIDL baseline changed; review prior capital recognition')
    header = None
    for line in path.read_text().splitlines():
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        if cells[0] == 'Venue':
            header = cells
        if cells[0] == 'E10' and header:
            row = dict(zip(header, cells, strict=True))
            def amount(key, row=row):
                return D(row[key].replace('$', '').replace(',', ''))
            capital, income = amount('period_inflow'), amount('actual_rev')
            if amount('value_eom') - amount('value_som') - capital != income:
                raise ValueError('Published BUIDL income does not reconcile to its capital flow')
            if amount('sd_revenue') != income or amount('revenue') != 0:
                raise ValueError('Historical BUIDL correction requires wholly Sky-direct income')
            return {'path': str(path.relative_to(ROOT)), 'sha256': digest,
                    'capital_inflow': capital, 'income': income}
    raise ValueError('Published BUIDL baseline is missing its venue row')


def reconcile_events(events: list[dict], prior: dict) -> dict:
    prime = load_prime_by_id('grove', config_dir=ROOT / 'config')
    venue = next(v for v in prime.venues if v.id == 'E10')
    policy = policy_for('grove', 'E10')
    # An isolated historical calculation only. Keep production recognition
    # from September, preserving both the existing true-up and boundary costs.
    policy = {**policy, 'recognition_start': START,
              'history_start': policy['cof_history_start']}
    holder = prime.alm[Chain.ETHEREUM].hex
    for event in events:
        expected = ((policy['share_token'], holder, policy['request_receiver'])
                    if event['kind'] == 'request'
                    else (policy['cash_token'], policy['cash_payer'], holder))
        if (event['token'], event['sender'], event['recipient']) != expected:
            raise ValueError('BUIDL evidence has an unexpected asset or counterparty')
    period = Period(START, END, {Chain.ETHEREUM: CLOSING_BLOCK})
    ledger = match_redemptions(venue, period, policy, events)
    if any(START <= date.fromisoformat(e['date']) <= END for e in ledger.unmatched_cash):
        raise ValueError('Unmatched historical BUIDL cash must be reviewed')
    costs = defaultdict(D)
    for settlement in ledger.settlements:
        costs[settlement['cash']['date'][:7]] -= D(settlement['revenue_adjustment_usd'])
    paid = {month: D(amount) for month, amount in prior['buidl_realized_fee_credit_by_month_usd'].items()}
    recorded = D(prior['buidl_realized_fee_credit_usd'])
    if not recorded.is_finite() or any(not x.is_finite() for x in paid.values()):
        raise ValueError('Non-finite prior BUIDL fee credit')
    if sum(paid.values(), D(0)) != recorded:
        raise ValueError('Prior BUIDL monthly fee credits do not reconcile')
    total = sum(costs.values(), D(0))
    additional = total - recorded
    # The same filter also omitted small SHARE EXITS from historical capital,
    # manufacturing a principal loss in Sky's SDE income. Restoring that
    # capital reverses an already-booked benefit to Grove; a fee-only credit
    # would therefore be the wrong net settlement adjustment.
    requests = {r['event_id']: r for settlement in ledger.settlements for r in settlement['requests']}
    requests.update({r['event_id']: r for r in ledger.outstanding})
    capital_debit = D(0)
    capital_corrections = {}
    affected = {r['date'][:7] for r in ledger.capital_outflows}
    for month in sorted(affected):
        baseline = published_baseline(month)
        monthly_requests = [r for r in requests.values() if r['date'].startswith(month)]
        all_exits = sum((D(r['carrying_value_usd']) for r in monthly_requests), D(0))
        filtered = sum((D(r['carrying_value_usd']) for r in monthly_requests
                        if D(r['shares']) >= venue.min_transfer_amount_usd), D(0))
        if baseline['capital_inflow'] == -all_exits:
            delta = D(0)
        elif baseline['capital_inflow'] == -filtered:
            delta = all_exits - filtered
        else:
            raise ValueError(f'Cannot attribute historical BUIDL capital at {month}')
        capital_debit += delta
        capital_corrections[month] = {
            **{key: str(value) for key, value in baseline.items()},
            'corrected_capital_inflow': str(-all_exits),
            'restored_sky_income': str(delta),
            'corrected_income_before_cash_costs': str(baseline['income'] + delta),
        }
    net_sky_adjustment = capital_debit - additional
    months = {month: {'actual_cost': str(costs.get(month, D(0))),
                      'already_recorded_credit': str(paid.get(month, D(0))),
                      'additional_credit': str(costs.get(month, D(0)) - paid.get(month, D(0)))}
              for month in sorted(costs.keys() | paid.keys())}
    return {
        'status': 'calculated_not_booked', 'prime': 'grove',
        'scope': '2026-01-01 through 2026-08-31 cash costs and published small-share capital omissions',
        'coverage_start': str(START), 'coverage_end': str(END),
        'actual_cost': str(total), 'already_recorded_fee_credit': str(recorded),
        'additional_credit_unrounded': str(additional),
        'capital_restoration_debit': str(capital_debit),
        'capital_corrections': capital_corrections,
        'net_sky_adjustment_unrounded': str(net_sky_adjustment),
        'proposed_sky_adj': str(net_sky_adjustment.quantize(D('.01'), rounding=ROUND_HALF_UP)),
        'months': months, 'settlements': list(ledger.settlements),
        'unsettled_at_cutoff': list(ledger.outstanding),
        'excluded': 'Existing CoF credits, September cash costs and September transition markdown; no rate-methodology changes',
    }


def reconcile() -> dict:
    if hashlib.sha256(PRIOR.read_bytes()).hexdigest() != PRIOR_SHA256:
        raise ValueError('Prior fee-credit ledger changed; review the already-recorded settlement baseline')
    result = reconcile_events(json.loads(EVENTS.read_text())['events'],
                              json.loads(PRIOR.read_text())['grove'])
    result['inputs'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [EVENTS, PRIOR]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    payload = json.dumps(reconcile(), indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    else:
        print(payload, end='')


if __name__ == '__main__':
    main()
