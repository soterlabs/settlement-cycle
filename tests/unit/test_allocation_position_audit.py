import importlib
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory as H

START = date(2026, 1, 1)
END = date(2026, 1, 31)


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    return importlib.import_module('audit_allocation_positions').audit


def batch(n, *movements, day=START, minted='0'):
    return B(str(n), day, day.toordinal() * 86400 + n, 'ethereum', n,
             tuple(movements), D(minted))


def movement(account, before, change, income='0'):
    return M(account, D(before), D(change), D(income))


def run(audit, *rows):
    return audit(H(rows, {'V': 'shares'}, {}, {'V': ['pending']}), 'V', START, END)


def test_passive_growth_does_not_increase_capital_and_return_separates_gain(audit):
    r = run(audit, batch(1, movement('shares', '0', '100')),
            batch(2, movement('shares', '110', '-55')))
    assert D(r['capital_added']) == 100
    assert D(r['cost_basis_released']) == 50
    assert D(r['closing_capital']) == 50
    assert D(r['transactions'][1]['external_value_out']) == 55


def test_income_shares_are_not_capital(audit):
    r = run(audit, batch(1, movement('shares', '0', '100')),
            batch(2, movement('shares', '100', '10', '10')),
            batch(3, movement('shares', '110', '-55')))
    assert D(r['explicit_rewards_excluded']) == 10
    assert D(r['closing_capital']) == 50


def test_t_plus_three_redemption_retains_capital_until_cash_arrives(audit):
    r = run(audit, batch(1, movement('shares', '0', '100')),
            batch(2, movement('shares', '110', '-110'), movement('pending', '0', '110'),
                  day=date(2026, 1, 28)),
            batch(3, movement('pending', '110', '-110'), movement('cash', '0', '110'),
                  day=date(2026, 1, 31)))
    assert D(r['daily']['2026-01-30']) == 100
    assert D(r['closing_capital']) == 0
    assert D(r['capital_added']) == 100


def test_subscription_delivery_is_not_a_second_capital_deposit(audit):
    r = run(audit, batch(1, movement('pending', '0', '100')),
            batch(2, movement('pending', '100', '-100'), movement('shares', '0', '99')))
    assert D(r['capital_added']) == 100
    assert D(r['cost_basis_released']) == 1  # Execution fee consumed, not cash withdrawn.
    assert D(r['closing_capital']) == 99


def test_opening_cost_is_not_opening_market_value(audit):
    r = run(audit, batch(1, movement('shares', '0', '100'), day=date(2025, 12, 1)),
            batch(2, movement('shares', '120', '-60')))
    assert D(r['opening_capital']) == 100
    assert D(r['capital_added']) == 0
    assert D(r['cost_basis_released']) == 50


def test_unrelated_ilk_repayment_does_not_change_allocation(audit):
    r = run(audit, batch(1, movement('shares', '0', '100')),
            batch(2, movement('cash', '50', '-50'), minted='-50'))
    assert D(r['closing_capital']) == 100
    assert len(r['transactions']) == 1


def test_missing_exit_is_not_silently_reset(audit):
    with pytest.raises(ValueError, match='Unexplained reset'):
        run(audit, batch(1, movement('shares', '0', '100')),
            batch(2, movement('shares', '0', '20')))


def test_duplicate_transaction_is_rejected(audit):
    row = batch(1, movement('shares', '0', '100'))
    with pytest.raises(ValueError, match='Duplicate'):
        run(audit, row, row)


def test_checkpoint_reproduces_year_without_old_history(audit):
    old = batch(1, movement('shares', '0', '100'), day=date(2025, 12, 1))
    new = batch(2, movement('shares', '120', '-60'))
    original = run(audit, old, new)
    h = H((new,), {'V': 'shares'}, {}, {'V': ['pending']})
    resumed = audit(h, 'V', START, END, opening_checkpoint=original['opening_checkpoint'])
    assert resumed['daily'] == original['daily']
    assert resumed['closing_capital'] == original['closing_capital']
    with pytest.raises(ValueError, match='opening history'):
        audit(H((old, new), {'V': 'shares'}, {}, {'V': ['pending']}), 'V', START, END,
              opening_checkpoint=original['opening_checkpoint'])
