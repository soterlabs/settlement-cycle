#!/usr/bin/env python3
"""Trace isolated vault deposits through empty ALM cash and authenticated bridges.

This is a bounded provenance audit, not a policy for assigning mixed cash.
It counts gross contributions, never outstanding debt or closing capital.
"""
import argparse
import gzip
import hashlib
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from settle.compute.executed_spell_capital import apply_executed_spells
from settle.normalize.allocation_history_cache import load_history


def audit_cash_sources(history, venue, cash_venue, start, end):
    target = history.venue_accounts[venue]
    cash = history.venue_accounts[cash_venue]
    if target == cash or end < start:
        raise ValueError('Invalid allocation/cash or period')
    batches = sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index))
    origins = {}
    clean_receipt = None
    recent_cash = []
    seen = set()
    rows = []
    totals = {}
    for b in batches:
        if b.day > end:
            break
        if b.identity in seen:
            raise ValueError('Duplicate transaction')
        seen.add(b.identity)
        cm = next((m for m in b.movements if m.account == cash), None)
        vm = next((m for m in b.movements if m.account == target), None)
        if vm and vm.change - vm.external_income > 0 and b.day >= start:
            amount = vm.change - vm.external_income
            row = {'transaction': b.identity, 'day': str(b.day), 'contribution': str(amount),
                   'classification': 'mixed_or_existing_cash'}
            # An authenticated receipt into empty cash can fund several
            # deposits. Retain it only while every spend goes to this vault.
            if (cm and cm.external_income == 0 and cm.change == -amount
                    and clean_receipt is not None
                    and cm.value_before == clean_receipt[2] and amount <= cm.value_before
                    and len([m for m in b.movements if m.change > 0]) == 1
                    and not b.external_funding and not b.funding_assumption and b.minted == 0):
                receipt, rm, remaining = clean_receipt
                claims = [m for m in receipt.movements if m.change < 0
                          and m.account.startswith(('cctp:', 'cctpv2:'))]
                if (rm.value_before == 0 and rm.external_income == 0
                        and not receipt.external_funding and not receipt.funding_assumption
                        and receipt.minted == 0
                        and len([m for m in receipt.movements if m.change > 0]) == 1
                        and sum((-m.change for m in claims), D(0)) == rm.change
                        and all(m.account in origins and origins[m.account][1] == -m.change
                                for m in claims)):
                    row['classification'] = 'authenticated_bridge_to_empty_cash_to_vault'
                    row['cash_receipt'] = receipt.identity
                    row['bridge_receipt_total'] = str(rm.change)
                    row['receipt_remaining_after_deposit'] = str(remaining - amount)
                    # Sources describe the entire receipt, not another deposit;
                    # only contribution is summed in totals.
                    row['bridge_sources'] = [
                        {'claim': m.account, 'amount': str(-m.change),
                         'transaction': origins[m.account][0].identity,
                         'draw_candidates_by_ilk': {
                             k: str(v) for k, v in origins[m.account][0].minted_by_ilk.items() if v > 0}}
                        for m in claims]
            # A second bounded shape: redeem this vault into empty cash,
            # receive one authenticated bridge, then redeposit the full cash
            # balance. The redeemed cash includes gains; never label it debt.
            if (row['classification'] == 'mixed_or_existing_cash' and len(recent_cash) == 2
                    and cm and cm.change == -amount and cm.value_before == amount
                    and cm.external_income == 0 and b.minted == 0
                    and not b.external_funding and not b.funding_assumption
                    and all(m.account in (cash, target) or m.change == 0 for m in b.movements)):
                (redemption, returned), (receipt, bridged) = recent_cash
                claims = [m for m in receipt.movements if m.change < 0
                          and m.account.startswith(('cctp:', 'cctpv2:'))]
                disposed = [m for m in redemption.movements if m.account == target]
                if (returned.value_before == 0 and returned.change > 0
                        and bridged.value_before == returned.change and bridged.change > 0
                        and returned.change + bridged.change == amount
                        and returned.external_income == bridged.external_income == 0
                        and len(disposed) == 1 and disposed[0].change == -returned.change
                        and disposed[0].external_income == 0
                        and all(m.account in (cash, target) or m.change == 0
                                for m in redemption.movements)
                        and all(m.account == cash or m in claims or m.change == 0
                                for m in receipt.movements)
                        and all(x.minted == 0 and not x.external_funding and not x.funding_assumption
                                for x in (redemption, receipt))
                        and sum((-m.change for m in claims), D(0)) == bridged.change
                        and all(m.account in origins and origins[m.account][1] == -m.change
                                for m in claims)):
                    row.update(classification='vault_redemption_plus_authenticated_bridge',
                               redemption_transaction=redemption.identity,
                               redeemed_cash_including_gains=str(returned.change),
                               cash_receipt=receipt.identity, bridged_cash=str(bridged.change),
                               bridge_sources=[
                                   {'claim': m.account, 'amount': str(-m.change),
                                    'transaction': origins[m.account][0].identity} for m in claims])
            totals[row['classification']] = totals.get(row['classification'], D(0)) + amount
            rows.append(row)
        if cm and cm.change:
            recent_cash = [*recent_cash[-1:], (b, cm)]
            if cm.change > 0:
                clean_receipt = (b, cm, cm.value_before + cm.change) if cm.value_before == 0 else None
            elif (vm and vm.change - vm.external_income == -cm.change
                  and rows and rows[-1]['transaction'] == b.identity
                  and rows[-1]['classification'] == 'authenticated_bridge_to_empty_cash_to_vault'):
                clean_receipt = (*clean_receipt[:2], cm.value_before + cm.change)
            else:
                clean_receipt = None
        for m in b.movements:
            if m.account.startswith(('cctp:', 'cctpv2:')) and m.change > 0:
                if m.account in origins:
                    raise ValueError('Reused bridge claim')
                origins[m.account] = (b, m.change)
    return {'venue': venue, 'cash_venue': cash_venue, 'start': str(start), 'end': str(end),
            'scope': __doc__.strip(),
            'totals': {k: str(v) for k, v in totals.items()}, 'contributions': rows}


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
        p.error('Output must not overwrite history')
    with gzip.open(args.history, 'rt') as f:
        key = json.loads(next(f))['fingerprint']
    h = load_history(args.history, key)
    if h is None:
        raise ValueError('Invalid history')
    result = audit_cash_sources(apply_executed_spells(h), args.venue, args.cash_venue,
                                args.start, args.end)
    result['input_sha256'] = hashlib.sha256(args.history.read_bytes()).hexdigest()
    result['source_sha256'] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [Path(__file__), *sorted(Path('src/settle/compute').glob('*.py'))]}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['totals'], indent=2))


if __name__ == '__main__':
    main()
