#!/usr/bin/env python3
"""Audit allocation entrypoints independently of a full funding replay.

This proves draw-to-deposit links in normalized transactions. It does not
certify current debt ownership: repayments elsewhere can refinance an intact
facility, and a cash return may release either borrowed capital or earnings.
Only cash-principal facility accounts are accepted, never a market-value mark.
"""

import argparse
import gzip
import hashlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from settle.compute.spark_anchorage_correction import correct_spark_anchorage_round_trip
from settle.compute.spark_anchorage_scenarios import apply_anchorage_scenario
from settle.normalize.allocation_history_cache import load_history


def audit(history, venue, start, end, *, verified_draws=None):
    account = history.venue_accounts[venue]
    if not account.startswith('facility:') or end < start:
        raise ValueError('Requires a cash-principal facility and a valid period')
    verified = {} if verified_draws is None else verified_draws
    if any(not amount.is_finite() or amount < 0 for amount in verified.values()):
        raise ValueError('Invalid verified facility draw')
    applied = set()
    balance = D(0)
    direct = D(0)
    debt_floor = {}
    deposits = D(0)
    returns = D(0)
    witnesses = []
    daily = {}
    seen = set()
    batches = sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index))
    cursor = 0
    # Exact debt arithmetic includes 45-decimal RAD conversions.
    with localcontext() as ctx:
        ctx.prec = 80
        for ordinal in range(start.toordinal(), end.toordinal() + 1):
            day = date.fromordinal(ordinal)
            while cursor < len(batches) and batches[cursor].day <= day:
                b = batches[cursor]
                cursor += 1
                if b.identity in seen:
                    raise ValueError('Duplicate transaction')
                seen.add(b.identity)
                # Even if another allocation/cash account paid the ilk, allow
                # the whole repayment to refinance this facility first. This
                # gives a conservative floor, without the replay's proportional
                # refinancing policy. Never add cumulative draw turnover as debt.
                if sum(b.minted_by_ilk.values(), D(0)) != b.minted:
                    raise ValueError('Missing or inconsistent per-ilk debt movements')
                for ilk, amount in b.minted_by_ilk.items():
                    if amount < 0:
                        debt_floor[ilk] = max(D(0), debt_floor.get(ilk, D(0)) + amount)
                selected = [m for m in b.movements if m.account == account]
                if not selected:
                    continue
                if len(selected) != 1 or len({m.account for m in b.movements}) != len(b.movements):
                    raise ValueError('Duplicate account movement')
                m = selected[0]
                if not m.preserve_basis or m.external_income or m.value_before != balance:
                    raise ValueError('Facility principal continuity or classification changed')
                balance += m.change
                if balance < 0:
                    raise ValueError('Facility return exceeds deposits')
                if m.change < 0:
                    returns -= m.change
                    # Conservative release: every return can remove direct-funded basis first.
                    direct = max(D(0), direct + m.change)
                    for ilk in debt_floor:
                        debt_floor[ilk] = max(D(0), debt_floor[ilk] + m.change)
                    witnesses.append({'transaction': b.identity, 'day': str(b.day),
                                      'principal_change': str(m.change), 'direct_draw_link': '0',
                                      'classification': 'principal_return'})
                    continue
                if not m.change:
                    continue
                deposits += m.change
                others = [x for x in b.movements if x.account != account]
                funding = b.minted_by_ilk
                # Exact transaction conservation bounds the fresh borrowing that
                # must reach this destination: first allow every other destination
                # to consume the draw. Existing cash is not presumed borrowed.
                eligible = (
                    b.minted > 0 and len(funding) == 1
                    and sum(funding.values(), D(0)) == b.minted
                    and not b.external_funding and not b.funding_assumption
                    and not any(x.external_income for x in others)
                    and abs(sum((x.change for x in b.movements), D(0)) - b.minted) <= D('1e-12')
                )
                other_destinations = sum((max(D(0), x.change) for x in others), D(0))
                linked = min(m.change, max(D(0), b.minted - other_destinations)) if eligible else D(0)
                if b.identity in verified:
                    # Caller authenticates an isolated cash route from fresh
                    # borrowing to this facility. Replace, never add twice.
                    linked = verified[b.identity]
                    if len(funding) != 1 or linked > m.change:
                        raise ValueError('Verified facility contribution exceeds deposit')
                    eligible = True
                    applied.add(b.identity)
                direct += linked
                if linked:
                    ilk = next(iter(funding))
                    # A proven gross draw can exceed net borrowing if a wipe
                    # follows the deposit. Origin survives, but do not assert
                    # that the repaid amount is still outstanding debt.
                    debt_floor[ilk] = debt_floor.get(ilk, D(0)) + min(linked, max(D(0), b.minted))
                witnesses.append({'transaction': b.identity, 'day': str(b.day),
                                  'principal_change': str(m.change), 'direct_draw_link': str(linked),
                                  'ilk': next(iter(funding)) if eligible else None,
                                  'classification': 'receipt_verified_draw' if b.identity in verified else 'same_transaction_draw' if linked
                                  else 'requires_source_attribution'})
            daily[str(day)] = {'facility_principal': str(balance),
                              'direct_origin_remaining_before_refinancing': str(direct),
                              'outstanding_direct_debt_floor_by_ilk': {k: str(v) for k, v in debt_floor.items()}}
        if set(verified) != applied:
            raise ValueError('Verified facility deposit missing from period')
        return {
            'venue_id': venue, 'account': account, 'start': str(start), 'end': str(end),
            'scope': 'Conditional on complete, correctly classified normalized history. '
                     'Outstanding debt floors deduct every same-ilk repayment against this '
                     'facility first, and every principal return against direct funding first. '
                     'Not an exact debt allocation or certified borrowing cost.',
            'deposits_through_end': str(deposits), 'principal_returns_through_end': str(returns),
            'closing_facility_principal': str(balance),
            'direct_origin_remaining_before_refinancing': str(direct),
            'closing_origin_not_proved_by_this_audit': str(balance - direct),
            'outstanding_direct_debt_floor_by_ilk': {k: str(v) for k, v in debt_floor.items()},
            'gross_same_transaction_draws': str(sum((D(w['direct_draw_link']) for w in witnesses), D(0))),
            'daily': daily, 'transactions': witnesses,
        }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('history', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--venue', required=True)
    p.add_argument('--start', type=date.fromisoformat, required=True)
    p.add_argument('--end', type=date.fromisoformat, required=True)
    p.add_argument('--anchorage-published-principal', action='store_true',
                   help='Use existing MSC full-principal treatment for the July/August receipts')
    args = p.parse_args()
    if args.output.resolve() == args.history.resolve():
        p.error('Output must not overwrite history')
    with gzip.open(args.history, 'rt') as src:
        key = json.loads(next(src))['fingerprint']
    history = load_history(args.history, key)
    if history is None:
        raise ValueError('Invalid history snapshot')
    history = correct_spark_anchorage_round_trip(history)
    if args.anchorage_published_principal:
        if args.venue != 'S23':
            p.error('Anchorage principal policy only applies to S23')
        history = apply_anchorage_scenario(history, 'all-principal')
        # The operator explicitly accepted the published MSC full-principal
        # treatment. Remove only the scenario helper's now-inapplicable warning;
        # retain any independent Savings or other funding assumption.
        note = 'Unconfirmed Anchorage principal/interest split: all-principal; QUESTIONS.md S33'
        history = replace(history, batches=tuple(replace(b, funding_assumption=(
            (b.funding_assumption or '').replace(note, '').strip('; ') or None
        )) for b in history.batches))
    result = audit(history, args.venue, args.start, args.end)
    result['principal_policy'] = ('Existing MSC full-principal classification, operator accepted; '
        'not an independently confirmed counterparty split' if args.anchorage_published_principal
        else 'Normalized snapshot classifications')
    sources = [args.history, Path(__file__),
               Path('src/settle/compute/spark_anchorage_correction.py'),
               Path('src/settle/compute/spark_anchorage_scenarios.py')]
    result['input_hashes'] = {str(s): hashlib.sha256(s.read_bytes()).hexdigest() for s in sources}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('daily', 'transactions', 'input_hashes')}, indent=2))


if __name__ == '__main__':
    main()
