import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_stac_capital import (
    CASH,
    DEPOSIT_WALLET,
    HOLDER,
    ISSUES,
    PAYMENTS,
    PENDING,
    SHARES,
    TOKEN,
    USDC,
    link_grove_stac_subscriptions,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/grove_stac_subscription_events.json').read_text())


def test_exact_subscription_wallet_payments_and_share_deliveries():
    for tx, block, amount in PAYMENTS:
        matches = [r for r in ROWS if r['transaction_hash'] == tx and r['address'] == USDC
                   and r['topic1'][-40:] == HOLDER[2:] and r['topic2'][-40:] == DEPOSIT_WALLET[2:]]
        assert len(matches) == 1 and matches[0]['block_number'] == block
        assert D(int(matches[0]['data'], 16)) / 10**6 == amount
    for tx, block in ISSUES:
        matches = [r for r in ROWS if r['transaction_hash'] == tx and r['address'] == TOKEN
                   and int(r['topic1'], 16) == 0 and r['topic2'][-40:] == HOLDER[2:]]
        assert len(matches) == 1 and matches[0]['block_number'] == block
        assert D(int(matches[0]['data'], 16)) / 10**6 == D('50000')
    assert PAYMENTS[0][2] + PAYMENTS[1][2] == PAYMENTS[2][2] == D('50000000')
    # The test-payment transaction also paid other entrypoints. None of those
    # funds belongs to STAC just because the transactions share a timestamp.
    test_rows = [r for r in ROWS if r['transaction_hash'] == PAYMENTS[0][0] and r['address'] == USDC
                 and r['topic1'][-40:] == HOLDER[2:]]
    assert len(test_rows) == 3


def history(earned_test_payment=False):
    batches = []
    for tx, block, amount in PAYMENTS:
        stamp = next(r['block_time'] for r in ROWS if r['transaction_hash'] == tx)
        day = datetime.fromtimestamp(stamp, UTC).date()
        if tx == PAYMENTS[0][0] and earned_test_payment:
            batches.append(CapitalBatch('known-income', day, stamp - 1, 'ethereum', block - 1,
                (AssetMovement(CASH, D(0), amount, external_income=amount),)))
            cash = AssetMovement(CASH, amount, -amount)
            minted = D(0)
        else:
            cash, minted = AssetMovement(CASH, D(0), D(0)), amount
        batches.append(CapitalBatch('ethereum:' + tx, day, stamp, 'ethereum', block,
                                   (cash,), minted))
    for n, (tx, block) in enumerate(ISSUES):
        stamp = next(r['block_time'] for r in ROWS if r['transaction_hash'] == tx)
        batches.append(CapitalBatch('ethereum:' + tx, datetime.fromtimestamp(stamp, UTC).date(),
            stamp, 'ethereum', block, (AssetMovement(SHARES, D(n) * 50_000_000, D(50_000_000)),)))
    return CapitalHistory(tuple(batches), {'E7': SHARES}, {})


def test_delayed_issuance_preserves_borrowing_and_known_earnings():
    h = history(earned_test_payment=True)
    linked = link_grove_stac_subscriptions(h)
    assert linked == link_grove_stac_subscriptions(linked)
    r = replay_history(h, date(2025, 12, 16), date(2025, 12, 18), quantify_uncertainty=True)
    assert r.ledger.account(SHARES).borrowed == D('99999000')
    assert r.ledger.account(SHARES).value == D('100000000')
    assert r.ledger.account(PENDING).borrowed == 0
    assert r.ledger.drawn == D('99999000')
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert r.funding_bounds_daily[date(2025, 12, 18)][SHARES]['unattributed'].low == D('99999000')
    assert r.ledger.realised_principal_loss == 0


def test_pinned_pending_subscription_has_basis_without_future_issuance():
    h = history()
    source = next(b for b in h.batches if b.identity == 'ethereum:' + PAYMENTS[0][0])
    pending = replace(h, batches=(source,))
    r = replay_history(pending, source.day, source.day)
    assert r.ledger.account(PENDING).borrowed == 1000
    assert not r.unmatched_receipts and not r.unmatched_outflows


@pytest.mark.parametrize('fault', ['missing_test', 'underfunded', 'duplicate', 'changed_issue', 'wrong_order'])
def test_incomplete_or_changed_route_cannot_silently_create_basis(fault):
    h = history()
    first, *rest = h.batches
    if fault == 'missing_test':
        h = replace(h, batches=tuple(rest))
    elif fault == 'underfunded':
        h = replace(h, batches=(replace(first, minted=D(0)), *rest))
    elif fault == 'duplicate':
        h = replace(h, batches=(*h.batches, first))
    elif fault == 'changed_issue':
        issue = h.batches[-1]
        h = replace(h, batches=(*h.batches[:-1], replace(issue, movements=(
            replace(issue.movements[0], change=D('49000000')),))))
    else:
        h = replace(h, batches=(*h.batches[:3], replace(h.batches[3], timestamp=1), h.batches[4]))
    with pytest.raises(ValueError, match=r'Grove STAC|Duplicate'):
        replay_history(h, date(2025, 12, 16), date(2025, 12, 18))


def test_already_linked_history_cannot_append_raw_route_events():
    h = history()
    linked = link_grove_stac_subscriptions(h)
    with pytest.raises(ValueError, match='append raw'):
        link_grove_stac_subscriptions(replace(linked, batches=(*linked.batches, h.batches[-1])))
