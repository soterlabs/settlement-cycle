"""Borrowed basis and identifiable gains in a bounded, mixed cash window.

Withdrawals use the cash balance's weighted-average mix. No receipt is selected
by matching its amount to a later repayment. Unknown receipts stay unresolved.
"""
from decimal import Decimal as D

from audit_allocation_positions import audit

ZERO = D(0)
EPS = D('1e-8')


def gain_window(history, window, account, venues, ledgers, start, end):
    if not window['starts_from_empty_cash']:
        return None
    index = {b.identity: b for b in history.batches}
    owned = {v: {history.venue_accounts[v], *history.custody_accounts.get(v, [])} for v in venues}
    balance = gain = ZERO
    evidence = []
    for row in window['transactions']:
        b = index[row['transaction']]
        ms = [m for m in b.movements if m.account == account]
        if len(ms) != 1:
            raise ValueError('Missing cash movement in origin window')
        m = ms[0]
        if abs(m.value_before - balance) > EPS or m.value_before + m.change < -EPS:
            raise ValueError('Cash origin window is discontinuous')
        added_gain = ZERO
        source_venue = None
        if m.change < 0:
            if m.external_income or balance <= 0:
                return None  # Net income/spend needs finer transaction ordering.
            gain *= max(ZERO, (balance + m.change) / balance)
        else:
            if not ZERO <= m.external_income <= m.change:
                return None
            added_gain = m.external_income
            others = [x for x in b.movements if x.account != account and x.change]
            candidates = [v for v, accounts in owned.items()
                          if account not in accounts and others
                          and all(x.account in accounts and x.change < 0
                                  and not x.external_income for x in others)]
            if (not added_gain and len(candidates) == 1 and not b.minted
                    and not b.external_funding and not b.funding_assumption):
                source_venue = candidates[0]
                if source_venue not in ledgers:
                    ledgers[source_venue] = audit(history, source_venue, start, end)
                events = [x for x in ledgers[source_venue]['transactions'] if x['transaction'] == b.identity]
                if len(events) != 1:
                    raise ValueError('Missing source allocation disposal')
                if D(events[0]['capital_added']) == 0:
                    added_gain = max(ZERO, m.change - D(events[0]['cost_basis_released']))
            gain += added_gain
        balance += m.change
        if gain < -EPS or gain > balance + EPS:
            raise ValueError('Cash gains exceed cash balance')
        evidence.append({'transaction': b.identity, 'source_venue': source_venue,
                         'cash_change': str(m.change), 'identified_gain_added': str(added_gain),
                         'remaining_identified_gains': str(gain), 'cash_balance': str(balance)})
    return {'cash_balance': balance, 'identified_gains': gain, 'transactions': evidence}


def cash_origin(history, batch, claim, amount, direct_origin, origin_trace, window,
                ledgers, start, end):
    """Only the sole cash leg funds the bridge remainder beyond its draw."""
    other = [m for m in batch.movements if m.account != claim and m.change]
    if (len(other) != 1 or other[0].change >= 0 or other[0].external_income
            or batch.external_funding or batch.funding_assumption):
        return None
    cash = other[0]
    spent = -cash.change
    if abs(direct_origin['borrowed'] + spent - amount) > EPS:
        return None
    if cash.account not in origin_trace['closing_borrowed_by_account']:
        return None
    borrowed = D(origin_trace['withdrawals'].get(batch.identity, {}).get(cash.account, '0'))
    gains = gain_window(history, window, cash.account, origin_trace['venues'], ledgers, start, end)
    if gains is None:
        return None
    if abs(gains['cash_balance'] - cash.value_before) > EPS or cash.value_before < spent:
        raise ValueError('Cash source window does not match bridge spend')
    gain = gains['identified_gains'] * spent / cash.value_before
    if borrowed + gain > spent + EPS:
        raise ValueError('Borrowed cash and gains overlap')
    return {
        'origin': {'borrowed': direct_origin['borrowed'] + borrowed,
                   'reinvested_gains': gain, 'unresolved': max(ZERO, spent - borrowed - gain)},
        'evidence': {
            'cash_account': cash.account, 'cash_spent': str(spent),
            'borrowed_cash': str(borrowed), 'gain_cash': str(gain),
            'policy': 'Weighted-average cash mix; no FIFO or repayment-to-receipt amount matching.',
            'gain_window': gains['transactions'],
        },
    }
