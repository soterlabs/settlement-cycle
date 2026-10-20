#!/usr/bin/env python3
"""Attribute a newly opened vault's capital to authenticated funding origins.

Requires bounded bridge/cash routes. Same-transaction draws are accepted only
when the bridge claim is the sole destination and transaction value conserves.
Other source capital remains unresolved. Withdrawals release each origin's
weighted-average cost; reinvesting the resulting gains never creates debt.
This diagnostic does not assign later ilk repayments or calculate financing.
"""
import argparse
import gzip
import hashlib
import json
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_allocation_cash_sources import audit_cash_sources
from audit_allocation_positions import audit

from settle.compute.executed_spell_capital import apply_executed_spells
from settle.normalize.allocation_history_cache import load_history

ZERO = D(0)
EPS = D('1e-8')


def bridge_origin(batch, claim, amount):
    ms = [m for m in batch.movements if m.account == claim]
    if len(ms) != 1 or ms[0].change != amount or ms[0].external_income:
        raise ValueError('Bridge source amount mismatch')
    others = [m for m in batch.movements if m.account != claim]
    eligible = (
        batch.minted > 0 and len(batch.minted_by_ilk) == 1
        and sum(batch.minted_by_ilk.values(), ZERO) == batch.minted
        and not batch.external_funding and not batch.funding_assumption
        and all(m.change <= 0 and m.external_income == 0 for m in others)
        and abs(sum((m.change for m in batch.movements), ZERO) - batch.minted) <= EPS
        and batch.minted <= amount
    )
    direct = batch.minted if eligible else ZERO
    return {'borrowed': direct, 'reinvested_gains': ZERO, 'unresolved': amount - direct}


def prior_cash_window(history, source, account):
    """Evidence since this cash account was last empty, without a FIFO policy."""
    rows = []
    starts_empty = False
    owners = {a: v for v, a in history.venue_accounts.items()}
    for venue, accounts in history.custody_accounts.items():
        for a in accounts:
            owners.setdefault(a, venue)
    for b in sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index)):
        if b.identity == source.identity:
            return {'starts_from_empty_cash': starts_empty, 'transactions': rows}
        ms = [m for m in b.movements if m.account == account and m.change]
        if not ms:
            continue
        if len(ms) != 1:
            raise ValueError('Duplicate cash movement')
        m = ms[0]
        if m.value_before == 0:
            rows = []
            starts_empty = True
        rows.append({
            'transaction': b.identity, 'day': str(b.day),
            'cash_before': str(m.value_before), 'cash_change': str(m.change),
            'explicit_cash_income': str(m.external_income),
            'other_movements': [
                {'account': x.account, 'venue': owners.get(x.account),
                 'change': str(x.change), 'income': str(x.external_income)}
                for x in b.movements if x.account != account and x.change],
            'draw_by_ilk': {k: str(v) for k, v in b.minted_by_ilk.items()},
        })
    raise ValueError('Cash source transaction missing from history')


def attribute(history, venue, cash_venue, start, end):
    with localcontext() as ctx:
        ctx.prec = 60
        return _attribute(history, venue, cash_venue, start, end)


def _attribute(history, venue, cash_venue, start, end):
    local = audit(history, venue, start, end)
    if D(local['opening_capital']) != 0:
        raise ValueError('Requires zero opening capital or a separately verified origin checkpoint')
    routes = audit_cash_sources(history, venue, cash_venue, start, end)
    routes = {r['transaction']: r for r in routes['contributions']}
    batches = {b.identity: b for b in history.batches}
    if len(batches) != len(history.batches):
        raise ValueError('Duplicate history transaction')
    components = dict.fromkeys(('borrowed', 'reinvested_gains', 'unresolved'), ZERO)
    returns = {}
    used_returns = set()
    used_claims = {}
    source_proofs = {}
    transactions = []
    for row in local['transactions']:
        if row['day'] < str(start):
            continue
        tx = row['transaction']
        released = D(row['cost_basis_released'])
        added = D(row['capital_added'])
        incoming = dict.fromkeys(components, ZERO)
        if released and added:
            raise ValueError('Mixed deposit/withdrawal requires explicit origin attribution')
        if released:
            total = sum(components.values(), ZERO)
            if total <= 0 or released > total + EPS:
                raise ValueError('Origin cost release exceeds capital')
            principal = {k: value * released / total for k, value in components.items()}
            components = {k: components[k] - principal[k] for k in components}
            cash_out = D(row['external_value_out'])
            # A loss reduces recovered principal; positive realized gains stay
            # separate even when reinvested immediately into the same vault.
            recovered = {k: v * min(D(1), cash_out / released) for k, v in principal.items()}
            recovered['reinvested_gains'] += max(ZERO, cash_out - released)
            returns[tx] = recovered
        if added:
            route = routes.get(tx)
            if route is None or D(route['contribution']) != added:
                raise ValueError('Missing or mismatched deposit route')
            kind = route['classification']
            if kind == 'authenticated_bridge_to_empty_cash_to_vault':
                fraction = added / D(route['bridge_receipt_total'])
            elif kind == 'vault_redemption_plus_authenticated_bridge':
                fraction = D(1)
                returned_tx = route['redemption_transaction']
                if returned_tx not in returns or returned_tx in used_returns:
                    raise ValueError('Missing or reused redemption')
                incoming = dict(returns[returned_tx])
                used_returns.add(returned_tx)
                if abs(sum(incoming.values(), ZERO) - D(route['redeemed_cash_including_gains'])) > EPS:
                    raise ValueError('Recycled cash does not match withdrawal')
            else:
                raise ValueError('Unresolved local deposit route')
            for source in route['bridge_sources']:
                claim, amount = source['claim'], D(source['amount'])
                b = batches[source['transaction']]
                if b.timestamp >= batches[tx].timestamp:
                    raise ValueError('Funding source must precede deposit')
                origin = bridge_origin(b, claim, amount)
                used_claims[claim] = used_claims.get(claim, ZERO) + amount * fraction
                if used_claims[claim] > amount + EPS:
                    raise ValueError('Bridge principal allocated twice')
                for k, v in origin.items():
                    incoming[k] += v * fraction
                source_proofs[claim] = {
                    'transaction': b.identity, 'day': str(b.day), 'bridge_amount': str(amount),
                    'origin': {k: str(v) for k, v in origin.items()},
                    'draw_by_ilk': {k: str(v) for k, v in b.minted_by_ilk.items()},
                    'other_movements': [
                        {'account': m.account, 'value_before': str(m.value_before),
                         'change': str(m.change), 'income': str(m.external_income)}
                        for m in b.movements if m.account != claim and m.change],
                    'prior_source_windows': {
                        m.account: prior_cash_window(history, b, m.account)
                        for m in b.movements if m.change < 0},
                }
            if abs(sum(incoming.values(), ZERO) - added) > EPS:
                raise ValueError('Contribution origin does not conserve value')
            for k in components:
                components[k] += incoming[k]
        if abs(sum(components.values(), ZERO) - D(row['closing_capital'])) > EPS:
            raise ValueError('Funding components differ from local capital')
        transactions.append({
            'transaction': tx, 'day': row['day'], 'capital_added': str(added),
            'cost_basis_released': str(released),
            'incoming_origin': {k: str(v) for k, v in incoming.items()},
            'closing_origin': {k: str(v) for k, v in components.items()},
        })
    closing = D(local['closing_capital'])
    return {
        'scope': __doc__.strip(), 'venue': venue, 'start': str(start), 'end': str(end),
        'closing_capital': str(closing),
        'closing_origin': {k: str(v) for k, v in components.items()},
        'closing_percent': {k: str(v / closing * 100 if closing else ZERO) for k, v in components.items()},
        'source_proofs': source_proofs, 'transactions': transactions,
        'withdrawal_cash_origin': {tx: {k: str(v) for k, v in c.items()} for tx, c in returns.items()},
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history', type=Path, required=True)
    p.add_argument('--venue', required=True)
    p.add_argument('--cash-venue', required=True)
    p.add_argument('--start', type=date.fromisoformat, required=True)
    p.add_argument('--end', type=date.fromisoformat, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.resolve() == args.history.resolve():
        p.error('Output must not overwrite input history')
    with gzip.open(args.history, 'rt') as f:
        key = json.loads(next(f))['fingerprint']
    h = load_history(args.history, key)
    if h is None:
        raise ValueError('Invalid history')
    result = attribute(apply_executed_spells(h), args.venue, args.cash_venue, args.start, args.end)
    result['input_sha256'] = hashlib.sha256(args.history.read_bytes()).hexdigest()
    paths = [Path(__file__), Path('scripts/audit_allocation_positions.py'),
             Path('scripts/audit_allocation_cash_sources.py'),
             *sorted(Path('src/settle/compute').glob('*.py'))]
    result['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('closing_capital', 'closing_origin', 'closing_percent')}, indent=2))


if __name__ == '__main__':
    main()
