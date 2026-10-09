import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_agora_redemptions import (
    AUSD,
    CASH,
    HOLDER,
    SOURCE,
    SUBSCRIPTIONS,
    USDC,
    WALLET,
    link_grove_agora_subscriptions,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/grove_agora_subscription_events.json').read_text())


def row(tx):
    return next(r for r in ROWS if r['transaction_hash'] == tx)


def history(group):
    payments, receipts, _ = group
    first = row(payments[0][0])
    value = sum(a for _, _, a in payments)
    day = datetime.fromtimestamp(first['block_time'], UTC).date()
    batches = [CapitalBatch('funding', day, first['block_time'] - 1, 'ethereum',
        first['block_number'] - 1, (AssetMovement(CASH, D(0), value),), value)]
    for events, account, direction in ((payments, CASH, -1), (receipts, SOURCE, 1)):
        balance = value if direction == -1 else D(0)
        for tx, block, amount in events:
            r = row(tx)
            batches.append(CapitalBatch('ethereum:' + tx,
                datetime.fromtimestamp(r['block_time'], UTC).date(), r['block_time'],
                'ethereum', block, (AssetMovement(account, balance, direction * amount),),
                log_index=r['log_index']))
            balance += direction * amount
    return CapitalHistory(tuple(batches), {'AUSD': SOURCE, 'Cash': CASH}, {})


def test_reviewed_cash_payments_and_deliveries_are_disjoint_and_at_par():
    assert len(SUBSCRIPTIONS) == 20
    seen = set()
    for payments, receipts, same_token in SUBSCRIPTIONS:
        assert not same_token
        prior = (-1, -1)
        assert sum(a for _, _, a in payments) == sum(a for _, _, a in receipts)
        for send, events in ((True, payments), (False, receipts)):
            for tx, block, amount in events:
                r = row(tx)
                assert tx not in seen
                seen.add(tx)
                assert (block, r['log_index']) > prior
                prior = (block, r['log_index'])
                assert block == r['block_number']
                assert r['address'] == (USDC if send else AUSD)
                if send:
                    assert r['topic1'][-40:] == HOLDER[2:]
                    assert r['topic2'][-40:] == WALLET[2:]
                else:
                    assert r['topic2'][-40:] == HOLDER[2:]
                    assert r['topic1'][-40:] in (
                        'be009e220f0c8fc5749ac4632a3e51706f1dca4f',
                        '080f646713bce0da8c08770d407818de47639cf5')
                assert D(int(r['data'], 16)) / 10**6 == amount
    assert len(seen) == len(ROWS) == 51
    assert sum(a for p, _, _ in SUBSCRIPTIONS for _, _, a in p) == D('96850000')


@pytest.mark.parametrize('group', SUBSCRIPTIONS)
def test_subscription_preserves_existing_funding_and_clears_pending_claim(group):
    h = history(group)
    linked = link_grove_agora_subscriptions(h)
    assert linked == link_grove_agora_subscriptions(linked)
    r = replay_history(h, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.account(SOURCE).borrowed == sum(a for _, _, a in group[0])
    assert r.ledger.realised_principal_loss == 0
    assert not r.unmatched_receipts and not r.unmatched_outflows
    for claim in linked.custody_accounts['AUSD']:
        assert r.ledger.account(claim).borrowed == 0


def test_pending_subscription_is_owned_by_ausd_venue_and_excludes_earned_funding():
    h = history(SUBSCRIPTIONS[1])
    first = h.batches[0]
    h = replace(h, batches=(replace(first, minted=D('6000000'), movements=(
        replace(first.movements[0], external_income=D('999000')),)), h.batches[1]))
    linked = link_grove_agora_subscriptions(h)
    r = replay_history(h, h.batches[0].day, h.batches[-1].day)
    claim = linked.custody_accounts['AUSD'][0]
    assert r.ledger.account(claim).borrowed == D('6000000')
    assert r.ledger.account(claim).value == D('6999000')
    assert 'Cash' not in linked.custody_accounts


def test_delivery_without_cash_payment_cannot_create_principal():
    h = history(SUBSCRIPTIONS[1])
    h = replace(h, batches=(h.batches[-1],))
    with pytest.raises(ValueError, match='exact outstanding conversion'):
        link_grove_agora_subscriptions(h)
