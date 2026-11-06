from datetime import date
from decimal import Decimal as D

from settle.compute.allocation_capital import CapitalEvent, CapitalLedger, replay_history
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

DAY = date(2026, 8, 1)


def test_funding_origins_survive_earnings_and_reallocation():
    ledger = CapitalLedger()
    for event in [CapitalEvent('a', DAY, (0,), 'draw', D(60), destination='cash', ilk='A'),
                  CapitalEvent('b', DAY, (1,), 'draw', D(40), destination='cash', ilk='B'),
                  CapitalEvent('c', DAY, (2,), 'income', D(10), destination='cash'),
                  CapitalEvent('d', DAY, (3,), 'transfer', D(110), 'cash', 'vault')]:
        ledger.apply(event)
    assert ledger.account('vault').borrowed_by_ilk == {'A': D(60), 'B': D(40)}
    assert ledger.account('vault').borrowed == 100


def test_equity_repayment_does_not_refinance_another_ilk():
    ledger = CapitalLedger()
    for event in [CapitalEvent('a', DAY, (0,), 'draw', D(60), destination='vault-a', ilk='A'),
                  CapitalEvent('b', DAY, (1,), 'draw', D(40), destination='vault-b', ilk='B'),
                  CapitalEvent('c', DAY, (2,), 'income', D(10), destination='cash'),
                  CapitalEvent('d', DAY, (3,), 'repay', D(10), 'cash', ilk='A')]:
        ledger.apply(event)
    assert ledger.account('vault-a').borrowed_by_ilk == {'A': D(50)}
    assert ledger.account('vault-b').borrowed_by_ilk == {'B': D(40)}
    assert ledger.repaid_by_ilk == {'A': D(10)}


def test_simultaneous_draw_and_repayment_keeps_both_ilks():
    batches = (
        CapitalBatch('one', DAY, 1, 'ethereum', 1, (AssetMovement('cash', D(0), D(100)),),
                     D(100), minted_by_ilk={'A': D(100)}),
        CapitalBatch('two', DAY, 2, 'ethereum', 2, (), D(0),
                     minted_by_ilk={'A': D(-20), 'B': D(20)}),
    )
    replay = replay_history(CapitalHistory(batches, {'V': 'cash'}, {}), DAY, DAY)
    assert replay.ledger.drawn_by_ilk == {'A': D(100), 'B': D(20)}
    assert replay.ledger.repaid_by_ilk == {'A': D(20)}
    assert replay.daily_by_ilk[DAY]['cash'] == {'A': D(80), 'B': D(20)}


def test_realized_fee_reduces_each_origin_without_creating_capital():
    ledger = CapitalLedger()
    for event in [CapitalEvent('a', DAY, (0,), 'draw', D(60), destination='vault', ilk='A'),
                  CapitalEvent('b', DAY, (1,), 'draw', D(40), destination='vault', ilk='B'),
                  CapitalEvent('c', DAY, (2,), 'transfer', D(90), 'vault', 'cash', D(90))]:
        ledger.apply(event)
    assert ledger.account('cash').borrowed_by_ilk == {'A': D(54), 'B': D(36)}
    assert ledger.account('vault').borrowed == 0
    assert ledger.realised_principal_loss == 10


def test_unknown_repayment_propagates_to_other_holdings_of_its_ilk():
    batches = (
        CapitalBatch('draw-a', DAY, 1, 'ethereum', 1,
                     (AssetMovement('vault-a', D(0), D(100)),), D(100),
                     minted_by_ilk={'A': D(100)}),
        CapitalBatch('draw-b', DAY, 2, 'ethereum', 2,
                     (AssetMovement('vault-b', D(0), D(100)),), D(100),
                     minted_by_ilk={'B': D(100)}),
        CapitalBatch('unknown-repayment', DAY, 3, 'ethereum', 3, (), D(-10),
                     minted_by_ilk={'A': D(-10)}),
    )
    replay = replay_history(CapitalHistory(batches, {'A': 'vault-a', 'B': 'vault-b'}, {}), DAY, DAY)
    assert replay.ledger.account('vault-a').borrowed == 90
    assert replay.ledger.account('vault-b').borrowed == 100
    assert 'vault-a' in replay.uncertain_daily[DAY]
    assert 'vault-b' not in replay.uncertain_daily[DAY]
    assert replay.uncertain_repayments['unknown-repayment'] == [
        {'ilk': 'A', 'amount': D(10), 'affected_account_count': 1, 'affected_accounts_sample': ['vault-a']}]


def test_unknown_withdrawal_taints_refinancing_and_remaining_custody():
    batches = (
        CapitalBatch('draw', DAY, 1, 'ethereum', 1,
                     (AssetMovement('vault', D(0), D(100)),), D(100),
                     minted_by_ilk={'A': D(100)}),
        CapitalBatch('unknown', DAY, 2, 'ethereum', 2,
                     (AssetMovement('cash', D(0), D(20)),)),
        CapitalBatch('repay', DAY, 3, 'ethereum', 3,
                     (AssetMovement('cash', D(20), D(-20)),), D(-10),
                     minted_by_ilk={'A': D(-10)}),
    )
    replay = replay_history(CapitalHistory(batches, {'V': 'vault'}, {}), DAY, DAY)
    assert {'vault', 'unallocated:repay'} <= replay.uncertain_accounts
    assert replay.uncertain_repayments['repay'][0]['affected_accounts_sample'] == ['vault']


def test_known_interest_repayment_does_not_create_uncertainty():
    batches = (
        CapitalBatch('draw', DAY, 1, 'ethereum', 1,
                     (AssetMovement('vault', D(0), D(100)),), D(100),
                     minted_by_ilk={'A': D(100)}),
        CapitalBatch('interest', DAY, 2, 'ethereum', 2,
                     (AssetMovement('cash', D(0), D(10), D(10)),)),
        CapitalBatch('repay', DAY, 3, 'ethereum', 3,
                     (AssetMovement('cash', D(10), D(-10)),), D(-10),
                     minted_by_ilk={'A': D(-10)}),
    )
    replay = replay_history(CapitalHistory(batches, {'V': 'vault'}, {}), DAY, DAY)
    assert replay.ledger.account('vault').borrowed == 90
    assert not replay.uncertain_accounts
    assert not replay.uncertain_repayments


def test_grove_direct_issue_releases_subscription_once():
    from dataclasses import replace

    import pytest

    from settle.compute.grove_historical_capital import (
        AMOUNT,
        CLEANUP,
        ISSUE,
        PENDING,
        SHARES,
        SUBSCRIPTION,
        link_grove_initial_jaaa,
    )
    batches = (
        CapitalBatch(SUBSCRIPTION, DAY, 1, 'ethereum', 22990215,
                     (AssetMovement(PENDING, D(0), AMOUNT),), AMOUNT,
                     minted_by_ilk={'A': AMOUNT}),
        CapitalBatch(ISSUE, DAY, 2, 'ethereum', 22991336,
                     (AssetMovement(SHARES, D(0), AMOUNT),)),
        CapitalBatch(CLEANUP, DAY, 3, 'ethereum', 23015872, ()),
        CapitalBatch('later-subscription', DAY, 4, 'ethereum', 23019398,
                     (AssetMovement(PENDING, AMOUNT, D(10)),), D(10),
                     minted_by_ilk={'A': D(10)}),
        CapitalBatch('later-claim', DAY, 5, 'ethereum', 23032149,
                     (AssetMovement(PENDING, AMOUNT + 10, D(-10)),
                      AssetMovement(SHARES, AMOUNT, D(10)))),
    )
    history = CapitalHistory(batches, {'E8': SHARES}, {}, {'E8': [PENDING]})
    linked = link_grove_initial_jaaa(history)
    assert link_grove_initial_jaaa(linked) == linked
    replay = replay_history(linked, DAY, DAY)
    assert replay.ledger.account(SHARES).borrowed == AMOUNT + 10
    assert replay.ledger.account(PENDING).borrowed == 0
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
    assert replay.ledger.drawn == AMOUNT + 10
    early = replace(history, batches=batches[:2])
    assert link_grove_initial_jaaa(early) == early
    with pytest.raises(ValueError, match='debt draws'):
        link_grove_initial_jaaa(replace(history, batches=(replace(batches[0], minted=D(0)), *batches[1:])))
    with pytest.raises(ValueError, match='later claim consumes'):
        link_grove_initial_jaaa(replace(history, batches=(*batches[:4],
            replace(batches[4], movements=(AssetMovement(PENDING, AMOUNT, D(-10)),)))))


def test_unknown_cross_ilk_repayment_marks_refinanced_holdings():
    batches = (
        CapitalBatch('draw-a', DAY, 1, 'ethereum', 1,
                     (AssetMovement('vault', D(0), D(100)),), D(100),
                     minted_by_ilk={'A': D(100)}),
        CapitalBatch('mixed-repay', DAY, 2, 'ethereum', 2, (), D(-60),
                     minted_by_ilk={'A': D(-100), 'B': D(40)}),
    )
    replay = replay_history(CapitalHistory(batches, {'V': 'vault'}, {}), DAY, DAY)
    assert replay.ledger.account('vault').borrowed_by_ilk == {'A': D(0), 'B': D(40)}
    assert 'vault' in replay.uncertain_accounts
    assert replay.uncertain_repayments['mixed-repay'][0]['affected_account_count'] == 1
