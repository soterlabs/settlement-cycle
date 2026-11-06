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


def test_group_receipt_is_alternative_bound_not_additional_capital(trace):
    history = H((b(1, m('a', '0', '60'), m('b', '0', '40'), draw='80'),),
                {'A': 'a', 'B': 'b'}, {}, {})
    result = trace(history, ('A', 'B'), DAY, verified_group_inflows={
        '1': {'accounts': ['a', 'b'], 'borrowed': D(90)}})
    assert D(result['closing_borrowed_by_account']['a']) == 54
    assert D(result['closing_borrowed_by_account']['b']) == 36
    smaller = trace(history, ('A', 'B'), DAY, verified_group_inflows={
        '1': {'accounts': ['a', 'b'], 'borrowed': D(10)}})
    assert sum(map(D, smaller['closing_borrowed_by_account'].values())) == 80


def test_individually_witnessed_group_routes_are_not_spread_between_messages(trace):
    history = H((b(1, m('a', '0', '60'), m('b', '0', '40'), draw='80'),),
                {'A': 'a', 'B': 'b'}, {}, {})
    proof = {'accounts': ['a', 'b'], 'borrowed': D(90), 'by_account': {'a': D(60), 'b': D(30)}}
    result = trace(history, ('A', 'B'), DAY, verified_group_inflows={'1': proof})
    assert result['closing_borrowed_by_account'] == {'a': '60', 'b': '30'}
    proof['by_account'] = {'a': D(40), 'b': D(50)}
    with pytest.raises(ValueError):
        trace(history, ('A', 'B'), DAY, verified_group_inflows={'1': proof})


@pytest.mark.parametrize('accounts,amount', [(['a'], '90'), (['a', 'b'], '101'),
                                           (['a', 'b'], 'NaN'), (['a', 'a'], '1')])
def test_group_proof_must_cover_all_incoming_accounts(trace, accounts, amount):
    history = H((b(1, m('a', '0', '60'), m('b', '0', '40'), draw='80'),),
                {'A': 'a', 'B': 'b'}, {}, {})
    with pytest.raises(ValueError):
        trace(history, ('A', 'B'), DAY, verified_group_inflows={
            '1': {'accounts': accounts, 'borrowed': D(amount)}})


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


@pytest.mark.parametrize('external_draw,other_use,expected', [('0', '20', '80'), ('100', '0', '0')])
def test_external_cash_payments_consume_sky_first(trace, external_draw, other_use, expected):
    from dataclasses import replace

    from settle.normalize.allocation_capital import ExternalFundingOperation as F

    # 100 new Sky debt, optional external cash, 100 assets retained. Existing
    # assets fund a payment in the first case; incoming external cash masks a
    # payment in the second. No repayment principal/interest split is needed.
    row = b(1, m('a', '0', '100'), m('old-other', other_use, '-' + other_use), draw='100')
    row = replace(row, external_funding=(F('draw', 'saver', D(external_draw)),
                                        F('repay', 'saver', D('1'))),
                  funding_assumption='Provisional repayment split')
    h = H((row,), {'A': 'a'}, {})
    assert D(trace(h, ('A',), DAY)['closing_borrowed_by_account']['a']) == 0
    result = trace(h, ('A',), DAY, bound_external_funding=True)
    assert D(result['closing_borrowed_by_account']['a']) == D(expected)
    # Deliberately alter the provisional principal/interest accounting split.
    changed = replace(row, external_funding=(F('draw', 'saver', D(external_draw)),
                                            F('repay', 'saver', D('0.5')),
                                            F('interest', 'saver', D('0.5'))))
    assert trace(replace(h, batches=(changed,)), ('A',), DAY, bound_external_funding=True) == result


def test_external_draw_never_becomes_sky_origin(trace):
    from dataclasses import replace

    from settle.normalize.allocation_capital import ExternalFundingOperation as F

    row = replace(b(1, m('a', '0', '100')), external_funding=(F('draw', 'saver', D(100)),))
    h = H((row,), {'A': 'a'}, {})
    assert D(trace(h, ('A',), DAY, bound_external_funding=True)['closing_borrowed_by_account']['a']) == 0


def test_external_bound_rejects_unknown_cash_operations(trace):
    from dataclasses import replace

    from settle.normalize.allocation_capital import ExternalFundingOperation as F

    row = replace(b(1, m('a', '0', '100'), draw='100'), external_funding=(F('unknown', 'saver', D(1)),))
    with pytest.raises(ValueError, match='Invalid external'):
        trace(H((row,), {'A': 'a'}, {}), ('A',), DAY, bound_external_funding=True)
