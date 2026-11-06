import importlib
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory as H

DAY = date(2026, 1, 1)
CLAIM = 'cctp:0:1:messenger'


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    return importlib.import_module('audit_bridged_allocation_origins')


def m(account, before, change, income='0'):
    return M(account, D(before), D(change), D(income))


def b(n, *movements, draw='0', chain='base'):
    return B(str(n), DAY, n, chain, n, tuple(movements), D(draw),
             minted_by_ilk={'ilk': D(draw)} if D(draw) else {})


def history():
    return H((b(1, m('eth-cash', '20', '-20'), m(CLAIM, '0', '120'), draw='100', chain='ethereum'),
              b(2, m(CLAIM, '120', '-120'), m('cash', '0', '120')),
              b(3, m('cash', '120', '-120'), m('vault', '0', '120')),
              b(4, m('vault', '132', '-66'), m('cash', '0', '66'))),
             {'V': 'vault', 'C': 'cash'}, {})


def test_bridge_preserves_partial_borrowing_and_does_not_capitalize_gains(module):
    r = module.attribute(history(), 'V', 'C', DAY, DAY)
    assert D(r['closing_capital']) == 60
    assert D(r['closing_borrowed']) == 50
    assert D(r['remaining_origin_to_classify']) == 10
    assert D(r['bridge_links'][0]['borrowed_at_receipt']) == 100
    assert sum(D(x['unclassified_closing_capital']) for x in r['surviving_unclassified_sources']) == 10


def test_batched_receipt_can_fund_vault_and_another_destination(module):
    h = H((b(1, m('eth-cash', '20', '-20'), m(CLAIM, '0', '100'), draw='80', chain='ethereum'),
           b(2, m(CLAIM, '100', '-100'), m('vault', '0', '80'), m('other', '0', '20'))),
          {'V': 'vault', 'C': 'cash'}, {})
    assert D(module.attribute(h, 'V', 'C', DAY, DAY)['closing_borrowed']) == 64


def test_unrelated_repayment_does_not_rewrite_original_funding(module):
    h = history()
    h = replace(h, batches=(*h.batches, b(5, m('unrelated', '500', '-500'), draw='-500')))
    assert D(module.attribute(h, 'V', 'C', DAY, DAY)['closing_borrowed']) == 50


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'amount', 'order'])
def test_bridge_link_must_be_unique_complete_and_conserved(module, mutation):
    h = history()
    if mutation == 'missing':
        h = replace(h, batches=h.batches[1:])
    elif mutation == 'duplicate':
        h = replace(h, batches=(*h.batches, replace(h.batches[1], identity='again', timestamp=5)))
    elif mutation == 'order':
        h = replace(h, batches=(replace(h.batches[0], timestamp=10), *h.batches[1:]))
    else:
        h = replace(h, batches=(h.batches[0], replace(h.batches[1], movements=(
            m(CLAIM, '120', '-119'), m('cash', '0', '120'))), *h.batches[2:]))
    with pytest.raises(ValueError, match='Bridge'):
        module.attribute(h, 'V', 'C', DAY, DAY)


def test_snapshot_selection_changes_only_reported_account_snapshots(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    from audit_scoped_borrowing_origins import trace
    h = history()
    full = trace(h, ('V', 'C'), DAY)
    compact = trace(h, ('V', 'C'), DAY, snapshot_accounts={'vault'})
    assert full['closing_borrowed_by_account'] == compact['closing_borrowed_by_account']
    assert full['withdrawals'] == compact['withdrawals']
    for a, b_ in zip(full['transactions'], compact['transactions'], strict=True):
        assert {k: v for k, v in a.items() if k != 'borrowed_by_account'} == {
            k: v for k, v in b_.items() if k != 'borrowed_by_account'}
        assert b_['borrowed_by_account'] == {'vault': a['borrowed_by_account']['vault']}
    with pytest.raises(ValueError, match='outside'):
        trace(h, ('V', 'C'), DAY, snapshot_accounts={'unknown'})


def test_reviewed_spark_base_history_reconciles_closing_origin(module):
    from settle.normalize.allocation_history_cache import load_history

    path = Path(__file__).resolve().parents[1] / 'fixtures/spark_base_morpho_origin_history.jsonl.gz'
    h = load_history(path, 'spark-base-origin-scope-v1')
    result = module.attribute(h, 'S34', 'S39', DAY, date(2026, 8, 31))
    assert D(result['closing_capital']).quantize(D('.01')) == D('283116139.25')
    assert D(result['closing_borrowed']).quantize(D('.01')) == D('254116181.11')
    assert D(result['remaining_origin_to_classify']).quantize(D('.01')) == D('28999958.13')
    assert len(result['bridge_links']) == 11482
    largest = 'base:0x57591b9fe87cdca67248e7fe0dbc11875775b04bd5ef49f1d34623b640e29a26'
    claims = [link for link in result['bridge_links'] if link['receipt'] == largest]
    assert len(claims) == 26
    assert sum(D(link['amount']) for link in claims) == D('25641472.293405')
    assert len({link['source'] for link in claims}) == 1
    bounded = module.attribute(h, 'S34', 'S39', DAY, date(2026, 8, 31), bound_external_funding=True)
    assert bounded['closing_capital'] == result['closing_capital']
    assert D(bounded['closing_borrowed']).quantize(D('.01')) == D('264631606.58')
    assert D(bounded['remaining_origin_to_classify']).quantize(D('.01')) == D('18484532.67')
