import importlib
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory as H


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    return importlib.import_module('audit_allocation_cash_sources').audit_cash_sources


def batch(n, *ms):
    return B(str(n), date(2026, 1, 1), n, 'base', n, tuple(ms), D(0))


def m(a, before, change):
    return M(a, D(before), D(change))


def run(audit, rows):
    return audit(H(tuple(rows), {'V': 'vault', 'C': 'cash'}, {}, {}),
                 'V', 'C', date(2026, 1, 1), date(2026, 1, 31))


def route(before='0', claim='cctpv2:ethereum:nonce'):
    return [
        batch(1, m('ethereum-cash', '100', '-100'), m('cctpv2:ethereum:nonce', '0', '100')),
        batch(2, m(claim, '100', '-100'), m('cash', before, '100')),
        batch(3, m('cash', str(D(before) + 100), '-100'), m('vault', '0', '100')),
    ]


def test_authenticated_route_retains_source_and_receipt(audit):
    r = run(audit, route())
    row = r['contributions'][0]
    assert r['totals'] == {'authenticated_bridge_to_empty_cash_to_vault': '100'}
    assert row['cash_receipt'] == '2'
    assert row['bridge_sources'][0]['transaction'] == '1'


@pytest.mark.parametrize('rows', [
    route(before='1'),
    route(claim='cctpv2:ethereum:other-nonce'),
    route()[1:],  # No origin: an equal receipt amount alone proves nothing.
])
def test_ambiguous_or_missing_origin_is_not_promoted(audit, rows):
    assert run(audit, rows)['totals'] == {'mixed_or_existing_cash': '100'}


def test_intervening_spend_prevents_reusing_previous_receipt(audit):
    rows = [*route()[:2],
        batch(3, m('cash', '100', '-50'), m('other', '0', '50')),
        batch(4, m('cash', '50', '-50'), m('vault', '0', '50')),
    ]
    assert run(audit, rows)['totals'] == {'mixed_or_existing_cash': '50'}


def test_one_bridge_can_fund_two_deposits_without_counting_receipt_twice(audit):
    rows = [*route()[:2],
            batch(3, m('cash', '100', '-10'), m('vault', '0', '10')),
            batch(4, m('cash', '90', '-90'), m('vault', '10', '90'))]
    r = run(audit, rows)
    assert r['totals'] == {'authenticated_bridge_to_empty_cash_to_vault': '100'}
    assert [x['contribution'] for x in r['contributions']] == ['10', '90']
    assert r['contributions'][1]['receipt_remaining_after_deposit'] == '0'


def test_redeemed_cash_and_bridge_remain_separately_identified(audit):
    rows = [
        batch(1, m('ethereum-cash', '90', '-90'), m('cctpv2:ethereum:nonce', '0', '90')),
        batch(2, m('vault', '10', '-10'), m('cash', '0', '10')),
        batch(3, m('cctpv2:ethereum:nonce', '90', '-90'), m('cash', '10', '90')),
        batch(4, m('cash', '100', '-100'), m('vault', '0', '100')),
    ]
    r = run(audit, rows)
    assert r['totals'] == {'vault_redemption_plus_authenticated_bridge': '100'}
    assert r['contributions'][0]['redeemed_cash_including_gains'] == '10'
    assert r['contributions'][0]['bridged_cash'] == '90'
    rows[1] = batch(2, m('other-vault', '10', '-10'), m('cash', '0', '10'))
    assert run(audit, rows)['totals'] == {'mixed_or_existing_cash': '100'}
