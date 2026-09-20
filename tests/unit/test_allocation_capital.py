from datetime import date
from decimal import Decimal as D

import pytest

from settle.compute.allocation_capital import (
    CapitalEvent,
    CapitalLedger,
    replay_capital,
    replay_history,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

DAY = date(2026, 8, 1)


def event(kind, amount, source=None, destination=None, value=None, n=0, day=DAY):
    return CapitalEvent(str(n), day, (n,), kind, D(amount), source, destination,
                        D(value) if value is not None else None)


def test_gain_is_not_borrowed_when_withdrawn_and_reallocated():
    ledger = CapitalLedger()
    for e in [event("draw", "100", destination="cash"),
              event("transfer", "100", "cash", "a"),
              event("transfer", "110", "a", "cash", "110"),
              event("transfer", "110", "cash", "b")]:
        ledger.apply(e)
    assert ledger.accounts["b"].value == D(110)
    assert ledger.accounts["b"].borrowed == D(100)
    assert ledger.accounts["a"].borrowed == ledger.accounts["cash"].borrowed == 0


def test_partial_withdrawal_carries_proportional_principal_and_gain():
    ledger = CapitalLedger()
    for e in [event("draw", "100", destination="a"),
              event("transfer", "55", "a", "b", "110")]:
        ledger.apply(e)
    assert ledger.accounts["a"].borrowed == D(50)
    assert ledger.accounts["b"].borrowed == D(50)
    assert ledger.accounts["b"].value == D(55)


def test_external_income_and_marks_never_create_principal():
    ledger = CapitalLedger()
    ledger.apply(event("income", "10", destination="cash"))
    ledger.apply(event("transfer", "10", "cash", "a"))
    ledger.apply(event("mark", "15", destination="a"))
    assert ledger.accounts["a"].borrowed == 0
    assert ledger.drawn == 0


def test_mixed_cash_uses_average_funding_fraction():
    ledger = CapitalLedger()
    ledger.apply(event("draw", "80", destination="cash"))
    ledger.apply(event("income", "20", destination="cash"))
    ledger.apply(event("transfer", "50", "cash", "a"))
    assert ledger.accounts["a"].borrowed == D(40)
    assert ledger.accounts["cash"].borrowed == D(40)


def test_realised_loss_cannot_fund_the_next_venue():
    ledger = CapitalLedger()
    ledger.apply(event("draw", "100", destination="a"))
    ledger.apply(event("transfer", "90", "a", "b", "90"))
    assert ledger.accounts["b"].borrowed == D(90)
    assert ledger.realised_principal_loss == D(10)
    assert ledger.drawn == D(100)


def test_equity_repayment_reduces_outstanding_position_basis():
    ledger = CapitalLedger()
    ledger.apply(event("draw", "100", destination="a"))
    ledger.apply(event("income", "20", destination="cash"))
    ledger.apply(event("repay", "20", "cash"))
    assert ledger.accounts["a"].borrowed == D(80)
    assert ledger.repaid == D(20)


def test_replay_seeds_from_history_and_preserves_intraday_reallocation():
    events = [
        event("draw", "100", destination="a", n=1, day=date(2026, 7, 1)),
        event("transfer", "110", "a", "cash", "110", n=2),
        event("transfer", "110", "cash", "b", n=3),
    ]
    ledger, daily = replay_capital(list(reversed(events)), DAY, date(2026, 8, 2))
    assert daily[DAY]["b"] == daily[date(2026, 8, 2)]["b"] == D(100)
    assert ledger.accounts["a"].borrowed == 0


def test_duplicate_event_and_overdraw_are_rejected():
    e = event("draw", "100", destination="cash")
    with pytest.raises(ValueError, match="Duplicate"):
        replay_capital([e, e], DAY, DAY)
    ledger = CapitalLedger()
    with pytest.raises(ValueError, match="exceeds"):
        ledger.apply(event("transfer", "100", "cash", "a"))


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity"])
def test_invalid_amounts_are_rejected(amount):
    with pytest.raises(ValueError, match="Invalid capital"):
        CapitalLedger().apply(event("draw", amount, destination="cash"))


def batch(n, movements, minted="0"):
    return CapitalBatch(str(n), DAY, n, "ethereum", n, tuple(movements), D(minted))


def test_atomic_swap_then_redemption_then_reinvestment():
    history = CapitalHistory((
        batch(1, [AssetMovement("a", D(0), D(100))], "100"),
        batch(2, [AssetMovement("a", D(110), D(-110)),
                  AssetMovement("cash", D(0), D(110))]),
        batch(3, [AssetMovement("cash", D(110), D(-110)),
                  AssetMovement("b", D(0), D(110))]),
    ), {"A": "a", "B": "b"}, {})
    replay = replay_history(history, DAY, DAY)
    assert replay.daily[DAY]["b"] == D(100)
    assert not replay.unmatched_receipts
    assert not replay.unmatched_outflows


def test_gift_in_same_transaction_as_deposit_does_not_acquire_basis():
    history = CapitalHistory((batch(1, [
        AssetMovement("a", D(0), D(100)),
        AssetMovement("gift", D(0), D(10), D(10)),
    ], "100"),), {}, {})
    replay = replay_history(history, DAY, DAY)
    assert replay.daily[DAY]["a"] == D(100)
    assert replay.daily[DAY]["gift"] == D(0)


def test_unmatched_receipt_is_not_inferred_to_be_a_loan():
    history = CapitalHistory((batch(1, [AssetMovement("a", D(0), D(100))]),), {}, {})
    replay = replay_history(history, DAY, DAY)
    assert replay.daily[DAY]["a"] == 0
    assert replay.unmatched_receipts == {"1": D(100)}
    assert "a" in replay.uncertain_accounts


def test_pending_custody_preserves_basis_and_does_not_fund_unrelated_receipt():
    history = CapitalHistory((
        batch(1, [AssetMovement("a", D(0), D(100))], "100"),
        batch(2, [AssetMovement("a", D(100), D(-100))]),
        batch(3, [AssetMovement("unrelated", D(0), D(100))]),
    ), {}, {})
    replay = replay_history(history, DAY, DAY)
    assert replay.daily[DAY]["unallocated:2"] == D(100)
    assert replay.daily[DAY]["unrelated"] == 0
    assert replay.unmatched_outflows == {"2": D(100)}
