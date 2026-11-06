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
    GROUPS,
    HOLDER,
    REDEEM_WALLET,
    SOURCE,
    USDC,
    WALLET,
    link_grove_agora_redemptions,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/grove_agora_redemption_events.json').read_text())


def row(tx):
    return next(r for r in ROWS if r['transaction_hash'] == tx)


def history(group):
    payments, receipts, _ = group
    first = row(payments[0][0])
    value = sum(a for _, _, a in payments)
    day = datetime.fromtimestamp(first['block_time'], UTC).date()
    batches = [CapitalBatch('funding', day, first['block_time'] - 1, 'ethereum',
        first['block_number'] - 1, (AssetMovement(SOURCE, D(0), value),), value)]
    for events, account, direction in ((payments, SOURCE, -1), (receipts, CASH, 1)):
        balance = value if direction == -1 else D(0)
        for tx, block, amount in events:
            r = row(tx)
            batches.append(CapitalBatch('ethereum:' + tx,
                datetime.fromtimestamp(r['block_time'], UTC).date(), r['block_time'],
                'ethereum', block, (AssetMovement(account, balance, direction * amount),),
                log_index=r['log_index']))
            balance += direction * amount
    return CapitalHistory(tuple(batches), {'AUSD': SOURCE, 'Cash': CASH}, {})


def test_reviewed_groups_match_canonical_wallets_tokens_and_six_decimal_amounts():
    assert len(GROUPS) == 29
    seen = set()
    for payments, receipts, same_token in GROUPS:
        assert not same_token
        prior = (-1, -1)
        for send, events in ((True, payments), (False, receipts)):
            for tx, block, amount in events:
                r = row(tx)
                assert tx not in seen
                seen.add(tx)
                assert (block, r['log_index']) > prior
                prior = (block, r['log_index'])
                assert block == r['block_number']
                assert r['address'] == (AUSD if send else USDC)
                assert r['topic1'][-40:] == (HOLDER if send else WALLET)[2:]
                assert r['topic2'][-40:] == (REDEEM_WALLET if send else HOLDER)[2:]
                assert D(int(r['data'], 16)) / 10**6 == amount
    assert len(seen) == len(ROWS) == 80
    assert sum(a for p, _, _ in GROUPS for _, _, a in p) == D('73397964.694525')
    assert sum(a for _, r, _ in GROUPS for _, _, a in r) == D('73397964.689863')


@pytest.mark.parametrize('group', GROUPS)
def test_redemptions_carry_basis_into_cash_with_only_observed_shortfall(group):
    h = history(group)
    linked = link_grove_agora_redemptions(h)
    assert linked == link_grove_agora_redemptions(linked)
    r = replay_history(h, h.batches[0].day, h.batches[-1].day)
    paid = sum(a for _, _, a in group[0])
    received = sum(a for _, _, a in group[1])
    assert r.ledger.account(CASH).borrowed == received
    assert r.ledger.realised_principal_loss == paid - received
    assert not r.unmatched_receipts and not r.unmatched_outflows


def test_cutoff_before_payout_keeps_principal_in_pending_redemption():
    h = history(GROUPS[6])  # Observed sub-cent shortfall is not realized early.
    h = replace(h, batches=h.batches[:-1])
    linked = link_grove_agora_redemptions(h)
    r = replay_history(h, h.batches[0].day, h.batches[-1].day)
    claim = linked.custody_accounts['AUSD'][0]
    assert r.ledger.account(claim).borrowed == D('3999683.294662')
    assert r.ledger.realised_principal_loss == 0


def test_earned_funding_is_not_reclassified_as_borrowed():
    h = history(GROUPS[2])
    first = h.batches[0]
    h = replace(h, batches=(replace(first, minted=D('3000000'), movements=(
        replace(first.movements[0], external_income=D('1000000')),)), *h.batches[1:]))
    r = replay_history(h, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.account(CASH).borrowed == D('3000000')
    assert r.ledger.account(CASH).value == D('4000000')


@pytest.mark.parametrize('fault', ['missing_payment', 'missing_payout', 'changed_amount', 'append_raw'])
def test_changed_or_incomplete_history_fails_instead_of_inventing_a_match(fault):
    h = history(GROUPS[2])
    if fault == 'missing_payment':
        h = replace(h, batches=(h.batches[0], *h.batches[2:]))
    elif fault == 'missing_payout':
        h = replace(h, batches=(*h.batches[:3], h.batches[-1]))
    elif fault == 'changed_amount':
        last = h.batches[-1]
        h = replace(h, batches=(*h.batches[:-1], replace(last, movements=(
            replace(last.movements[0], change=D(1)),))))
    else:
        linked = link_grove_agora_redemptions(h)
        h = replace(linked, batches=(*linked.batches, h.batches[-1]))
    with pytest.raises(ValueError, match=r'Grove Agora|append raw'):
        link_grove_agora_redemptions(h)
