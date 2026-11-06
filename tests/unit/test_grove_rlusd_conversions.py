import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_rlusd_conversions import (
    CASH,
    GROUPS,
    HOLDER,
    RLUSD,
    SOURCE,
    USDC,
    WALLET,
    link_grove_rlusd_conversions,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/grove_rlusd_conversion_events.json').read_text())


def row(tx):
    return next(r for r in ROWS if r['transaction_hash'] == tx)


def batch(tx, block, movements, minted=D(0)):
    r = row(tx)
    return CapitalBatch('ethereum:' + tx, datetime.fromtimestamp(r['block_time'], UTC).date(),
        r['block_time'], 'ethereum', block, tuple(movements), minted, log_index=r['log_index'])


def history(group):
    payments, receipts, same_token = group
    source = row(payments[0][0])
    amount = sum(a for _, _, a in payments)
    funding = CapitalBatch('funding', datetime.fromtimestamp(source['block_time'], UTC).date(),
        source['block_time'] - 1, 'ethereum', source['block_number'] - 1,
        (AssetMovement(SOURCE, D(0), amount),), amount)
    batches = [funding]
    value = amount
    for tx, block, paid in payments:
        batches.append(batch(tx, block, [AssetMovement(SOURCE, value, -paid)]))
        value -= paid
    cash = D(0)
    for tx, block, received in receipts:
        batches.append(batch(tx, block, [AssetMovement(SOURCE if same_token else CASH, cash, received)]))
        cash += received
    return CapitalHistory(tuple(batches), {'RL': SOURCE, 'Cash': CASH}, {})


def test_canonical_conversion_groups_and_source_recipient_shapes():
    assert len(GROUPS) == 13
    for payments, receipts, same_token in GROUPS:
        prior = (-1, -1)
        for outgoing, events in ((True, payments), (False, receipts)):
            for tx, block, amount in events:
                r = row(tx)
                order = (r['block_number'], r['log_index'])
                assert order > prior and block == r['block_number']
                prior = order
                assert r['address'] == (RLUSD if outgoing or same_token else USDC)
                assert r['topic1'][-40:] == (HOLDER if outgoing else WALLET)[2:]
                assert r['topic2'][-40:] == (WALLET if outgoing else HOLDER)[2:]
                assert D(int(r['data'], 16)) / 10**(18 if outgoing or same_token else 6) == amount
        assert sum(a for _, _, a in payments) >= sum(a for _, _, a in receipts)
    assert sum(len(r) for _, r, same in GROUPS if not same) == 29


@pytest.mark.parametrize('group', GROUPS)
def test_each_complete_conversion_conserves_borrowing_and_records_cash_shortfall(group):
    h = history(group)
    linked = link_grove_rlusd_conversions(h)
    assert linked == link_grove_rlusd_conversions(linked)
    start, end = h.batches[0].day, h.batches[-1].day
    r = replay_history(h, start, end)
    paid = sum(a for _, _, a in group[0])
    received = sum(a for _, _, a in group[1])
    destination = SOURCE if group[2] else CASH
    assert r.ledger.account(destination).borrowed == received
    assert r.ledger.realised_principal_loss == paid - received
    assert sum(a.borrowed for a in r.ledger.accounts.values()) + r.ledger.realised_principal_loss == paid
    assert not r.unmatched_receipts and not r.unmatched_outflows


def test_partial_payout_leaves_remaining_basis_pending_without_future_fee():
    group = GROUPS[1]  # $50m conversion split into seven cash receipts.
    h = history(group)
    # Only first payout is available at this snapshot.
    h = replace(h, batches=h.batches[:3])
    linked = link_grove_rlusd_conversions(h)
    r = replay_history(h, h.batches[0].day, h.batches[-1].day)
    first_cash = group[1][0][2]
    assert r.ledger.account(linked.custody_accounts['RL'][0]).borrowed == D(50_000_000) - first_cash
    assert r.ledger.account(CASH).borrowed == first_cash
    assert r.ledger.realised_principal_loss == 0


def test_payouts_in_same_block_and_overnight_settlement_are_preserved():
    h = history(GROUPS[-2])
    dates = {b.day for b in h.batches}
    assert len(dates) == 2
    assert h.batches[-4].block == h.batches[-3].block
    r = replay_history(h, min(dates), max(dates))
    assert r.ledger.account(CASH).borrowed == D(65_000_000)
    assert r.ledger.realised_principal_loss == 0


@pytest.mark.parametrize('fault', ['missing_payment', 'missing_payout', 'wrong_amount', 'wrong_order', 'append'])
def test_incomplete_or_changed_routes_cannot_erase_pending_capital(fault):
    h = history(GROUPS[1])
    if fault == 'missing_payment':
        h = replace(h, batches=(h.batches[0], *h.batches[2:]))
    elif fault == 'missing_payout':
        h = replace(h, batches=(*h.batches[:2], *h.batches[3:]))
    elif fault == 'wrong_amount':
        b = h.batches[-1]
        h = replace(h, batches=(*h.batches[:-1], replace(b, movements=(
            replace(b.movements[0], change=D(1)),))))
    elif fault == 'wrong_order':
        h = replace(h, batches=(*h.batches[:2], replace(h.batches[2], timestamp=1), *h.batches[3:]))
    else:
        linked = link_grove_rlusd_conversions(h)
        h = replace(linked, batches=(*linked.batches, h.batches[-1]))
    with pytest.raises(ValueError, match=r'Grove Ripple|append raw'):
        link_grove_rlusd_conversions(h)


def test_earned_funding_does_not_become_borrowed_on_cash_conversion():
    h = history(GROUPS[1])
    initial = h.batches[0]
    h = replace(h, batches=(replace(initial, minted=D('40000000'), movements=(
        AssetMovement(SOURCE, D(0), D('50000000'), external_income=D('10000000')),)), *h.batches[1:]))
    r = replay_history(h, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.account(CASH).borrowed == D('40000000')
    assert r.ledger.account(CASH).value == D('49990000')
    assert r.ledger.realised_principal_loss == 0
