#!/usr/bin/env python3
"""Allocation-local contributed capital, independent of other venues' debt.

Uses normalized execution amounts and reviewed custody links. Passive NAV
growth and explicitly classified rewards add no cost basis. Withdrawals release
weighted-average acquisition cost; the rest of the proceeds is gain/loss.
This is allocation capital, not proof that every contribution was borrowed.
"""

import argparse
import gzip
import hashlib
import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from settle.compute.executed_spell_capital import apply_executed_spells
from settle.normalize.allocation_history_cache import load_history


def audit(history, venue, start, end, *, opening_checkpoint=None):
    if end < start:
        raise ValueError('Invalid period')
    holding = history.venue_accounts[venue]
    accounts = {holding, *history.custody_accounts.get(venue, [])}
    basis = dict.fromkeys(accounts, D(0))
    last_value = dict.fromkeys(accounts, D(0))
    events = []
    daily = {}
    seen = set()
    # Only transactions touching this allocation are replayed. Other cash,
    # ilks and allocations do not change its capital balance.
    batches = sorted((b for b in history.batches if any(m.account in accounts for m in b.movements)),
                     key=lambda b: (b.timestamp, b.chain, b.block, b.log_index))
    opening = None
    opening_costs = None
    if opening_checkpoint is not None:
        if opening_checkpoint['date'] != str(start) or opening_checkpoint['venue'] != venue:
            raise ValueError('Checkpoint period or venue mismatch')
        seed = {k: D(v) for k, v in opening_checkpoint['basis_by_account'].items()}
        if set(seed) != accounts or any(not v.is_finite() or v < 0 for v in seed.values()):
            raise ValueError('Checkpoint accounts or amounts invalid')
        if any(b.day < start for b in batches):
            raise ValueError('Checkpoint cannot be combined with opening history')
        basis = seed
        opening_costs = dict(seed)
    additions = releases = rewards = D(0)
    cursor = 0
    with localcontext() as ctx:
        ctx.prec = 60
        day = start
        while day <= end:
            while cursor < len(batches) and batches[cursor].day <= day:
                b = batches[cursor]
                cursor += 1
                if b.identity in seen:
                    raise ValueError('Duplicate allocation transaction')
                seen.add(b.identity)
                if b.day >= start and opening is None:
                    opening = sum(basis.values(), D(0))
                    opening_costs = dict(basis)
                ms = [m for m in b.movements if m.account in accounts]
                if len({m.account for m in ms}) != len(ms):
                    raise ValueError('Duplicate account movement')
                incoming = []
                out_value = out_basis = gift = D(0)
                for m in ms:
                    if m.value_before < 0 or m.external_income < 0:
                        raise ValueError('Invalid position value or reward')
                    value = m.value_before + m.external_income
                    change = m.change - m.external_income
                    if m.value_before == 0 and basis[m.account] > D('1e-8'):
                        raise ValueError('Unexplained reset of a funded position')
                    last_value[m.account] = m.value_before + m.change
                    if last_value[m.account] < -D('1e-8'):
                        raise ValueError('Negative position after movement')
                    gift += m.external_income
                    if change > 0:
                        incoming.append((m.account, change))
                    elif change < 0:
                        if value <= 0 or -change > value + D('1e-8'):
                            raise ValueError('Withdrawal exceeds position')
                        removed = basis[m.account] * min(D(1), -change / value)
                        basis[m.account] -= removed
                        out_value -= change
                        out_basis += removed
                in_value = sum((amount for _, amount in incoming), D(0))
                carried = min(in_value, out_value) * out_basis / out_value if out_value else D(0)
                added = max(D(0), in_value - out_value)
                released = out_basis - carried
                for account, amount in incoming:
                    basis[account] += (carried + added) * amount / in_value
                if b.day >= start:
                    additions += added
                    releases += released
                    rewards += gift
                events.append({
                    'transaction': b.identity, 'day': str(b.day),
                    'capital_added': str(added), 'cost_basis_released': str(released),
                    'explicit_rewards_excluded': str(gift),
                    'external_value_out': str(max(D(0), out_value - in_value)),
                    'closing_capital': str(sum(basis.values(), D(0))),
                    # These are transaction-level candidates, not a claimed
                    # one-to-one match in a multi-route spell or mixed swap.
                    'source_candidates': {m.account: str(-(m.change - m.external_income))
                        for m in b.movements if m.account not in accounts
                        and m.change - m.external_income < 0} if added else {},
                    'draw_candidates_by_ilk': {k: str(v) for k, v in b.minted_by_ilk.items()
                                               if v > 0} if added else {},
                    'external_lender_candidates': [{'source': op.source, 'amount': str(op.amount)}
                        for op in b.external_funding if op.kind == 'draw'] if added else [],
                    'funding_assumption': b.funding_assumption,
                })
            if opening is None:
                opening = sum(basis.values(), D(0))
                opening_costs = dict(basis)
            daily[str(day)] = str(sum(basis.values(), D(0)))
            day += timedelta(days=1)
        closing = sum(basis.values(), D(0))
        error = opening + additions - releases - closing
        if abs(error) > D('1e-8'):
            raise ValueError('Allocation capital identity failed')
        return {'venue': venue, 'start': str(start), 'end': str(end),
                'opening_checkpoint': {'date': str(start), 'venue': venue,
                    'basis_by_account': {k: str(v) for k, v in sorted(opening_costs.items())}},
                'opening_capital': str(opening), 'capital_added': str(additions),
                'cost_basis_released': str(releases), 'closing_capital': str(closing),
                'explicit_rewards_excluded': str(rewards), 'identity_error': str(error),
                'closing_holding_capital': str(basis[holding]),
                'closing_custody_capital': str(closing - basis[holding]),
                'custody': {a: {'capital': str(basis[a]), 'last_observed_value': str(last_value[a])}
                            for a in sorted(accounts - {holding}) if basis[a] or last_value[a]},
                'daily': daily, 'transactions': events}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history', type=Path, required=True)
    p.add_argument('--venues', nargs='+', required=True)
    p.add_argument('--start', type=date.fromisoformat, required=True)
    p.add_argument('--end', type=date.fromisoformat, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.resolve() == args.history.resolve():
        p.error('Output must not overwrite history')
    with gzip.open(args.history, 'rt') as source:
        key = json.loads(next(source))['fingerprint']
    h = load_history(args.history, key)
    if h is None:
        raise ValueError('Invalid history')
    h = apply_executed_spells(h)
    allocations = {v: audit(h, v, args.start, args.end) for v in args.venues}
    # Subsequent audits need the opening checkpoint and current-year movements,
    # not the old debt/refinancing replay. Verify that property on real inputs.
    current_year = replace(h, batches=tuple(b for b in h.batches if b.day >= args.start))
    for venue, original in allocations.items():
        resumed = audit(current_year, venue, args.start, args.end,
                        opening_checkpoint=original['opening_checkpoint'])
        if resumed['daily'] != original['daily']:
            raise ValueError('Opening checkpoint fails to reproduce the capital timeline')
    result = {'scope': __doc__.strip(), 'opening_checkpoint_reproduction_verified': True,
              'limitations': ['Normalized execution values and income classifications are inputs.',
                'Source candidates are not independent proofs of borrowed funding.',
                'Releases include cost disposed through withdrawals, transfers or execution losses.',
                'No Sky cost-of-funds or published settlement calculation is changed.'],
              'allocations': allocations,
              'input_sha256': hashlib.sha256(args.history.read_bytes()).hexdigest(),
              'source_sha256': {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in [Path(__file__), *sorted(Path('src/settle/compute').glob('*.py'))]}}
    raw = (json.dumps(result, indent=2) + '\n').encode()
    args.output.write_bytes(gzip.compress(raw, mtime=0) if args.output.suffix == '.gz' else raw)
    print(json.dumps({v: {k: x for k, x in r.items() if k not in ('daily', 'transactions', 'custody', 'opening_checkpoint')}
                      for v, r in result['allocations'].items()}, indent=2))


if __name__ == '__main__':
    main()
