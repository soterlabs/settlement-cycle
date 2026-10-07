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
