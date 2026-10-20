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
def trace(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    return importlib.import_module('audit_scoped_borrowing_origins').trace


def m(a, before, change, income='0'):
    return M(a, D(before), D(change), D(income))


def b(n, *ms, draw='0'):
    return B(str(n), DAY, n, 'ethereum', n, tuple(ms), D(draw),
             minted_by_ilk={'ilk': D(draw)} if D(draw) else {})


def run(trace, rows):
    return trace(H(tuple(rows), {'A': 'a', 'B': 'b'}, {}, {}), ('A', 'B'), DAY)


def test_reallocation_carries_origin_without_turning_yield_into_debt(trace):
    r = run(trace, [
        b(1, m('a', '0', '100'), draw='100'),
        b(2, m('a', '110', '-110'), m('b', '0', '110')),
        b(3, m('b', '110', '-55')),
        b(4, m('unrelated-cash', '500', '-500'), draw='-500'),
    ])
    assert D(r['withdrawals']['3']['b']) == 50
    assert D(r['closing_borrowed_by_account']['b']) == 50


def test_known_swap_income_does_not_block_or_inflate_direct_draw(trace):
    r = run(trace, [b(1, m('a', '0', '100'), m('b', '0', '2', income='2'), draw='100')])
    assert D(r['closing_borrowed_by_account']['a']) == 100
    assert D(r['closing_borrowed_by_account']['b']) == 0


def test_competing_destination_gets_first_claim_on_draw(trace):
    r = run(trace, [b(1, m('a', '0', '100'), m('other', '0', '80'),
                       m('existing-cash', '100', '-100'), draw='80')])
    assert D(r['closing_borrowed_by_account']['a']) == 0


def test_execution_loss_is_not_fictitious_borrowed_assets(trace):
    r = run(trace, [b(1, m('a', '0', '99'), draw='100')])
    assert D(r['closing_borrowed_by_account']['a']) == 99


def test_unexplained_opening_value_is_not_assumed_borrowed(trace):
    r = run(trace, [b(1, m('a', '500', '-100'), m('b', '0', '100'))])
    assert all(D(v) == 0 for v in r['closing_borrowed_by_account'].values())


def test_pending_custody_preserves_origin(trace):
    rows = [b(1, m('a', '0', '100'), draw='100'),
            b(2, m('a', '110', '-110'), m('pending', '0', '110')),
            b(3, m('pending', '120', '-120'), m('b', '0', '120'))]
    h = H(tuple(rows), {'A': 'a', 'B': 'b'}, {}, {'A': ['pending']})
    assert D(trace(h, ('A', 'B'), DAY)['closing_borrowed_by_account']['b']) == 100
