import importlib
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory as H

DAY = date(2026, 1, 1)


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    return importlib.import_module('audit_cash_origin_windows')


def m(a, before, change, income='0'):
    return M(a, D(before), D(change), D(income))


def b(n, *ms):
    return B(str(n), DAY, n, 'ethereum', n, tuple(ms), D(0))


def window(*ids):
    return {'starts_from_empty_cash': True, 'transactions': [{'transaction': str(n)} for n in ids]}


def test_mixed_cash_repayment_preserves_proportional_gain_share(module):
    rows = [
        b(1, m('cash', '0', '30', '30')),
        b(2, m('cash', '30', '70')),
        b(3, m('cash', '100', '-40')),
        b(4, m('cash', '60', '-20'), m('claim', '0', '20')),
    ]
    h = H(tuple(rows), {'C': 'cash'}, {}, {})
    origin_trace = {'venues': ['C'], 'closing_borrowed_by_account': {'cash': '0'},
                    'withdrawals': {'4': {'cash': '8'}}}
    r = module.cash_origin(h, rows[-1], 'claim', D(20), {'borrowed': D(0)},
                           origin_trace, window(1, 2, 3), {}, DAY, DAY)
    assert r['origin'] == {'borrowed': D(8), 'reinvested_gains': D(6), 'unresolved': D(6)}


@pytest.mark.parametrize(('proceeds', 'expected_gain'), [('110', '10'), ('90', '0')])
def test_disposal_gain_uses_actual_cash_after_fees(module, proceeds, expected_gain):
    rows = [b(1, m('shares', '0', '100')),
            b(2, m('shares', '120', '-120'), m('cash', '0', proceeds))]
    h = H(tuple(rows), {'V': 'shares'}, {}, {})
    r = module.gain_window(h, window(2), 'cash', ['V'], {}, DAY, DAY)
    assert r['identified_gains'] == D(expected_gain)


def test_internal_custody_delivery_is_not_income(module):
    rows = [b(1, m('pending', '100', '-100'), m('cash', '0', '100'))]
    h = H(tuple(rows), {'C': 'cash'}, {}, {'C': ['pending']})
    r = module.gain_window(h, window(1), 'cash', ['C'], {}, DAY, DAY)
    assert r['identified_gains'] == 0


def test_missing_cash_movement_fails_closed(module):
    h = H((b(1, m('cash', '5', '10')),), {'C': 'cash'}, {}, {})
    with pytest.raises(ValueError, match='discontinuous'):
        module.gain_window(h, window(1), 'cash', ['C'], {}, DAY, DAY)


def test_overlapping_borrowed_basis_and_gain_is_rejected(module):
    rows = [b(1, m('cash', '0', '100', '100')),
            b(2, m('cash', '100', '-100'), m('claim', '0', '100'))]
    h = H(tuple(rows), {'C': 'cash'}, {}, {})
    origin_trace = {'venues': ['C'], 'closing_borrowed_by_account': {'cash': '0'},
                    'withdrawals': {'2': {'cash': '1'}}}
    with pytest.raises(ValueError, match='overlap'):
        module.cash_origin(h, rows[-1], 'claim', D(100), {'borrowed': D(0)},
                           origin_trace, window(1), {}, DAY, DAY)
