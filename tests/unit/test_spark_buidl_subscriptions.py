import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_buidl_subscriptions import (
    CASH,
    DEPOSIT_WALLET,
    HOLDER,
    SHARES,
    SUBSCRIPTIONS,
    TOKEN,
    USDC,
    link_spark_buidl_subscriptions,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/spark_buidl_subscription_events.json').read_text())


def test_subscriptions_match_canonical_cash_and_mint_events():
    previous = 0
    for pay, block, paid, tx, issued_block, delivered, _ in SUBSCRIPTIONS:
        cash = [r for r in ROWS if r['transaction_hash'] == pay]
        issue = [r for r in ROWS if r['transaction_hash'] == tx]
        assert len(cash) == len(issue) == 1
        c, i = cash[0], issue[0]
        assert c['address'] == USDC and c['block_number'] == block
        assert c['topic1'][-40:] == HOLDER[2:]
        assert c['topic2'][-40:] == DEPOSIT_WALLET[2:]
        assert D(int(c['data'], 16)) / 10**6 == paid
        assert i['address'] == TOKEN and i['block_number'] == issued_block
        assert int(i['topic1'], 16) == 0 and i['topic2'][-40:] == HOLDER[2:]
        assert D(int(i['data'], 16)) / 10**6 == delivered
        assert previous < c['block_time'] < i['block_time']
        previous = i['block_time']
    assert sum(r[2] for r in SUBSCRIPTIONS) == D('800100000')
    assert sum(r[5] for r in SUBSCRIPTIONS) == D('799525374.43')


def history():
    batches, value = [], D(0)
    deliveries = {r[3]: r[6] for r in SUBSCRIPTIONS}
    for row in ROWS:
        if row['block_number'] > SUBSCRIPTIONS[-1][4]:
            continue
        tx, stamp = row['transaction_hash'], row['block_time']
        amount = D(int(row['data'], 16)) / 10**6
        cash = row['address'] == USDC
        movement = AssetMovement(CASH, D(0), D(0)) if cash else AssetMovement(
            SHARES, value, amount, external_income=deliveries.get(tx, amount))
        if not cash:
            value += amount
        batches.append(CapitalBatch('ethereum:' + tx, datetime.fromtimestamp(stamp, UTC).date(),
            stamp, 'ethereum', row['block_number'], (movement,), amount if cash else D(0),
            minted_by_ilk={'SPARK': amount} if cash else {}))
    return CapitalHistory(tuple(batches), {'S19': SHARES}, {})


def test_full_paid_basis_survives_delivery_and_distributions_add_no_debt():
    h = history()
    linked = link_spark_buidl_subscriptions(h)
    assert linked == link_spark_buidl_subscriptions(linked)
    day = h.batches[-1].day
    result = replay_history(h, day, day, quantify_uncertainty=True)
    assert result.ledger.drawn == result.ledger.account(SHARES).borrowed == D('800100000')
    assert result.ledger.realised_principal_loss == 0
    assert not result.unmatched_receipts and not result.unmatched_outflows
    assert all(result.ledger.account(a).borrowed == 0 for a in linked.custody_accounts['S19'])
    assert result.funding_bounds_daily[day][SHARES]['SPARK'].low == D('800100000')
    # Ordinary small distributions remain earned funding, separate from the
    # exact initial test subscription whose old income label is overridden.
    for batch in h.batches:
        if batch.identity in {b.identity for b in linked.batches}:
            assert batch in linked.batches
    assert result.ledger.account(SHARES).value == (h.batches[-1].movements[0].value_before + h.batches[-1].movements[0].change)


def test_cutoff_keeps_pending_basis_and_unknown_mints_are_not_linked():
    h = history()
    first = h.batches[0]
    pending = replace(h, batches=(first,))
    linked = link_spark_buidl_subscriptions(pending)
    result = replay_history(pending, first.day, first.day)
    assert result.ledger.account(linked.custody_accounts['S19'][0]).borrowed == D('100000')
    # A similar receipt without the reviewed identity must remain unresolved.
    issue = replace(h.batches[1], identity='ethereum:unreviewed',
        movements=(replace(h.batches[1].movements[0], external_income=D(0)),))
    result = replay_history(replace(h, batches=(first, issue)), issue.day, issue.day)
    assert result.ledger.account(SHARES).borrowed == 0
    assert issue.identity in result.unmatched_receipts


@pytest.mark.parametrize('fault', ['missing_payment', 'no_funding', 'wrong_block', 'wrong_amount',
                                   'wrong_order', 'wrong_income', 'duplicate', 'append'])
def test_inconsistent_subscription_fails_closed(fault):
    h = history()
    batches = list(h.batches)
    if fault == 'missing_payment':
        batches.pop(0)
    elif fault == 'no_funding':
        batches[0] = replace(batches[0], minted=D(0), minted_by_ilk={})
    elif fault == 'wrong_block':
        batches[0] = replace(batches[0], block=1)
    elif fault == 'wrong_order':
        batches[1] = replace(batches[1], timestamp=1)
    elif fault in ('wrong_amount', 'wrong_income'):
        m = batches[1].movements[0]
        m = replace(m, change=m.change + 1) if fault == 'wrong_amount' else replace(m, external_income=D(0))
        batches[1] = replace(batches[1], movements=(m,))
    elif fault == 'duplicate':
        batches.append(batches[0])
    else:
        batches = [*link_spark_buidl_subscriptions(h).batches, batches[1]]
    with pytest.raises(ValueError, match=r'Spark BUIDL|Duplicate capital'):
        link_spark_buidl_subscriptions(replace(h, batches=tuple(batches)))


def test_earned_cash_payment_does_not_become_borrowed_capital():
    h = history()
    first, issue = h.batches[:2]
    income = replace(first, identity='earned-income', timestamp=first.timestamp - 1,
        minted=D(0), minted_by_ilk={}, movements=(
            AssetMovement(CASH, D(0), D('100000'), external_income=D('100000')),))
    payment = replace(first, minted=D(0), minted_by_ilk={}, movements=(
        AssetMovement(CASH, D('100000'), D('-100000')),))
    result = replay_history(replace(h, batches=(income, payment, issue)), issue.day, issue.day)
    assert result.ledger.drawn == result.ledger.account(SHARES).borrowed == 0
    assert result.ledger.account(SHARES).value == D('100000')
    assert not result.unmatched_receipts and not result.unmatched_outflows
