import gzip
import importlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory
from settle.normalize.allocation_history_cache import load_history

DAY = date(2026, 8, 1)
ACCOUNT = 'facility:ethereum:alm:warehouse'


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    return importlib.import_module('audit_facility_funding').audit


def batch(identity, opening, amount, draw, others=()):
    return B(identity, DAY, int(identity), 'ethereum', int(identity),
             (M(ACCOUNT, D(opening), D(amount), preserve_basis=True), *others),
             D(draw), minted_by_ilk={'ilk': D(draw)} if D(draw) else {})


def run(audit, *batches):
    return audit(CapitalHistory(batches, {'E42': ACCOUNT}, {}), 'E42', DAY, DAY)


def test_direct_draw_plus_existing_cash_only_certifies_draw(audit):
    r = run(audit, batch('1', '0', '100', '90', (M('cash', D(10), D(-10)),)))
    assert D(r['closing_facility_principal']) == 100
    assert D(r['direct_origin_remaining_before_refinancing']) == 90
    assert D(r['closing_origin_not_proved_by_this_audit']) == 10


def test_reallocation_does_not_create_new_draw_and_returns_reduce_link(audit):
    r = run(audit, batch('1', '0', '100', '100'),
            batch('2', '100', '-40', '0', (M('cash', D(0), D(40)),)),
            batch('3', '60', '40', '0', (M('cash', D(40), D(-40)),)))
    assert D(r['gross_same_transaction_draws']) == 100
    assert D(r['direct_origin_remaining_before_refinancing']) == 60
    assert D(r['closing_facility_principal']) == 100


def test_repayment_elsewhere_reduces_debt_floor_not_facility_principal(audit):
    deposit = batch('1', '0', '100', '100')
    repayment = B('2', DAY, 2, 'ethereum', 2, (), D(-30), minted_by_ilk={'ilk': D(-30)})
    r = run(audit, deposit, repayment)
    assert D(r['closing_facility_principal']) == 100
    assert D(r['direct_origin_remaining_before_refinancing']) == 100
    assert D(r['outstanding_direct_debt_floor_by_ilk']['ilk']) == 70


def test_other_ilk_repayment_does_not_retire_this_draw(audit):
    repayment = B('2', DAY, 2, 'ethereum', 2, (), D(-200), minted_by_ilk={'other': D(-200)})
    r = run(audit, batch('1', '0', '100', '100'), repayment)
    assert D(r['outstanding_direct_debt_floor_by_ilk']['ilk']) == 100


def test_multiple_destinations_only_certify_unavoidable_draw_share(audit):
    r = run(audit, batch('1', '0', '80', '60', (
        M('cash', D(40), D(-40)), M('other', D(0), D(20)))))
    assert D(r['gross_same_transaction_draws']) == 40


def test_unrelated_earnings_cannot_certify(audit):
    r = run(audit, batch('1', '0', '100', '100', (
        M('cash', D(0), D(0), external_income=D(1)),)))
    assert D(r['gross_same_transaction_draws']) == 0


def test_vat_rounding_is_tolerated_but_missing_cash_is_not(audit):
    r = run(audit, batch('1', '0', '100', '100.0000000000000000001'))
    assert D(r['gross_same_transaction_draws']) == 100
    r = run(audit, batch('1', '0', '100', '99'))
    assert D(r['gross_same_transaction_draws']) == 0


def test_receipt_proof_replaces_bound_and_preserves_principal_return_policy(audit):
    deposit = batch('1', '0', '100', '100', (M('other', D(0), D(20)),))
    returned = batch('2', '100', '-30', '0', (M('cash', D(0), D(30)),))
    history = CapitalHistory((deposit, returned), {'E42': ACCOUNT}, {})
    result = audit(history, 'E42', DAY, DAY, verified_draws={'1': D(100)})
    assert D(result['gross_same_transaction_draws']) == 100
    assert D(result['direct_origin_remaining_before_refinancing']) == 70
    assert result['transactions'][0]['classification'] == 'receipt_verified_draw'


@pytest.mark.parametrize('proofs', [{'1': D(101)}, {'missing': D(1)}, {'1': D('NaN')}])
def test_invalid_verified_facility_contribution_rejected(audit, proofs):
    history = CapitalHistory((batch('1', '0', '100', '100'),), {'E42': ACCOUNT}, {})
    with pytest.raises(ValueError):
        audit(history, 'E42', DAY, DAY, verified_draws=proofs)


@pytest.mark.parametrize('fault', ['duplicate', 'opening', 'income', 'mark'])
def test_invalid_facility_history_fails_closed(audit, fault):
    b = batch('1', '0', '100', '100')
    if fault == 'duplicate':
        rows = (b, b)
    else:
        m = b.movements[0]
        changes = {'opening': {'value_before': D(1)},
                   'income': {'external_income': D(1)}, 'mark': {'preserve_basis': False}}
        rows = (replace(b, movements=(replace(m, **changes[fault]),)),)
    with pytest.raises(ValueError):
        run(audit, *rows)


def test_actual_warehouse_draws_match_cash_deposits_and_survive_repayment_bound(audit):
    root = Path(__file__).parents[1] / 'fixtures'
    path = root / 'grove_warehouse_funding_history.jsonl.gz'
    with gzip.open(path, 'rt') as src:
        key = json.loads(next(src))['fingerprint']
    h = load_history(path, key)
    r = audit(h, 'E42', date(2026, 1, 1), date(2026, 8, 31))
    transfers = json.loads((root / 'grove_warehouse_deposit_transfers.json').read_text())
    assert len(transfers) == len(r['transactions']) == 9
    amounts = {'ethereum:' + t['transaction_hash']: D(int(t['data'], 16)) / 10**6 for t in transfers}
    assert all(t['address'] == '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
               and t['topic1'].endswith('491edfb0b8b608044e227225c715981a30f3a44e')
               and t['topic2'].endswith('3e23311f9ff660e3c3d87e4b7c207b3c3d7e04f0')
               for t in transfers)
    assert all(D(w['principal_change']) == amounts[w['transaction']] for w in r['transactions'])
    assert D(r['closing_facility_principal']) == D('304000001')
    assert D(r['direct_origin_remaining_before_refinancing']) == D('303957394.57882')
    assert sum(map(D, r['outstanding_direct_debt_floor_by_ilk'].values())) == D('141686399.554805')
