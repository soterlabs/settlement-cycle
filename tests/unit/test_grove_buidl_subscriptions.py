import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_buidl_subscriptions import (
    CASH,
    DEPOSIT_WALLET,
    HOLDER,
    SHARES,
    SUBSCRIPTIONS,
    TOKEN,
    USDC,
    link_grove_buidl_subscriptions,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/grove_buidl_subscription_events.json').read_text())


def test_reviewed_payments_and_issuances_match_raw_logs_and_fees():
    previous_issue = 0
    for payments, tx, block, delivered, fee in SUBSCRIPTIONS:
        for payment_tx, payment_block, amount in payments:
            rows = [r for r in ROWS if r['transaction_hash'] == payment_tx and r['address'] == USDC
                    and r['topic1'][-40:] == HOLDER[2:] and r['topic2'][-40:] == DEPOSIT_WALLET[2:]]
            assert len(rows) == 1 and rows[0]['block_number'] == payment_block
            assert D(int(rows[0]['data'], 16)) / 10**6 == amount
            assert previous_issue < rows[0]['block_time']
        issued = [r for r in ROWS if r['transaction_hash'] == tx and r['address'] == TOKEN
                  and int(r['topic1'], 16) == 0 and r['topic2'][-40:] == HOLDER[2:]]
        assert len(issued) == 1 and issued[0]['block_number'] == block
        assert issued[0]['block_time'] > rows[0]['block_time']
        assert D(int(issued[0]['data'], 16)) / 10**6 == delivered
        assert sum(amount for _, _, amount in payments) == delivered + fee
        previous_issue = issued[0]['block_time']
    assert len(SUBSCRIPTIONS) == 20
    assert sum(len(p) for p, *_ in SUBSCRIPTIONS) == 26
    assert sum(amount for p, *_ in SUBSCRIPTIONS for _, _, amount in p) == D('800000000')
    assert sum(fee for *_, fee in SUBSCRIPTIONS) == D('90000')


def history():
    batches = []
    value = D(0)
    for payments, tx, block, delivered, _ in SUBSCRIPTIONS:
        for payment_tx, payment_block, amount in payments:
            stamp = next(r['block_time'] for r in ROWS if r['transaction_hash'] == payment_tx)
            batches.append(CapitalBatch('ethereum:' + payment_tx, datetime.fromtimestamp(stamp, UTC).date(),
                stamp, 'ethereum', payment_block, (AssetMovement(CASH, D(0), D(0)),), amount,
                minted_by_ilk={'BLOOM': amount}))
        stamp = next(r['block_time'] for r in ROWS if r['transaction_hash'] == tx)
        batches.append(CapitalBatch('ethereum:' + tx, datetime.fromtimestamp(stamp, UTC).date(),
            stamp, 'ethereum', block, (AssetMovement(SHARES, value, delivered),)))
        value += delivered
    return CapitalHistory(tuple(batches), {'E10': SHARES}, {})


def test_issuance_preserves_full_paid_basis_even_when_fees_reduce_token_face():
    h = history()
    linked = link_grove_buidl_subscriptions(h)
    assert linked == link_grove_buidl_subscriptions(linked)
    r = replay_history(h, date(2026, 4, 13), date(2026, 4, 13), quantify_uncertainty=True)
    assert r.ledger.drawn == D('800000000')
    assert r.ledger.account(SHARES).borrowed == D('800000000')
    assert r.ledger.account(SHARES).value == D('799910000')
    assert r.ledger.realised_principal_loss == 0
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert sum(a.borrowed for a in r.ledger.accounts.values()) + r.ledger.realised_principal_loss == r.ledger.drawn
    assert all(r.ledger.account(a).borrowed == 0 for a in linked.custody_accounts['E10'])
    assert r.funding_bounds_daily[date(2026, 4, 13)][SHARES]['BLOOM'].low == D('800000000')


def test_open_subscription_and_unreviewed_mint_remain_distinct():
    h = history()
    first = h.batches[0]
    pending = replace(h, batches=(first,))
    linked = link_grove_buidl_subscriptions(pending)
    r = replay_history(pending, first.day, first.day)
    assert r.ledger.account(linked.custody_accounts['E10'][0]).borrowed == D(1000)
    unknown = replace(h.batches[2], identity='ethereum:unreviewed-mint')
    r = replay_history(replace(h, batches=(first, unknown)), unknown.day, unknown.day)
    assert r.ledger.account(SHARES).borrowed == 0
    assert 'ethereum:unreviewed-mint' in r.unmatched_receipts


@pytest.mark.parametrize('fault', ['missing_test', 'changed_fee', 'no_cash', 'wrong_order', 'append'])
def test_incomplete_or_mutated_route_is_rejected(fault):
    h = history()
    if fault == 'missing_test':
        h = replace(h, batches=h.batches[1:])
    elif fault == 'changed_fee':
        issue = h.batches[2]
        h = replace(h, batches=(*h.batches[:2], replace(issue, movements=(
            replace(issue.movements[0], change=D('50000000')),)), *h.batches[3:]))
    elif fault == 'no_cash':
        h = replace(h, batches=(replace(h.batches[0], minted=D(0), minted_by_ilk={}), *h.batches[1:]))
    elif fault == 'wrong_order':
        h = replace(h, batches=(*h.batches[:2], replace(h.batches[2], timestamp=1), *h.batches[3:]))
    else:
        linked = link_grove_buidl_subscriptions(h)
        h = replace(linked, batches=(*linked.batches, h.batches[2]))
    with pytest.raises(ValueError, match=r'Grove BUIDL|append raw'):
        link_grove_buidl_subscriptions(h)


def test_own_cash_in_subscription_never_becomes_borrowed_basis():
    h = history()
    first = h.batches[0]
    income = replace(first, identity='known-income', timestamp=first.timestamp - 1,
                     minted=D(0), minted_by_ilk={},
                     movements=(AssetMovement(CASH, D(0), D(1000), external_income=D(1000)),))
    payment = replace(first, minted=D(0), minted_by_ilk={},
                      movements=(AssetMovement(CASH, D(1000), D(-1000)),))
    h = replace(h, batches=(income, payment, *h.batches[1:3]))
    day = h.batches[-1].day
    r = replay_history(h, day, day)
    assert r.ledger.drawn == r.ledger.account(SHARES).borrowed == D('49999000')
    assert r.ledger.account(SHARES).value == D('49985000')
    assert not r.unmatched_receipts and not r.unmatched_outflows
