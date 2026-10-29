import copy
import gzip
import importlib.util
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location('vat_accrual', ROOT/'scripts/audit_spark_vat_accrual.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture(scope='module')
def saved():
    with gzip.open(ROOT/'tests/fixtures/spark_vat_accrual_events.json.gz','rt') as f:
        evidence=json.load(f)
    debt=json.loads((ROOT/'tests/fixtures/spark_vat_accrual_debt_control.json').read_text())
    return evidence,debt


def test_independent_vat_events_explain_all_august_cash_debt_differences(saved):
    r=audit.reconstruct(*saved)
    assert len(r['daily'])==31
    assert all(abs(D(d['non_cash_rate_accrual'])-D('75612301.038815520442580349418127135701354204000940874'))<D('1e-18') for d in r['daily'])
    assert all(abs(D(d['cash_draws_less_repayments'])+D(d['non_cash_rate_accrual'])-D(d['non_msc_debt']))<D('1e-17') for d in r['daily'])
    assert r['first_non_cash_accrual']['day']<'2026-08-01'
    assert r['last_non_cash_accrual']['day']=='2025-09-10'


def test_missing_fold_cannot_be_replaced_by_a_balancing_adjustment(saved):
    evidence,debt=saved
    changed={**evidence,'rows':list(evidence['rows'])}
    index=next(i for i,r in enumerate(changed['rows']) if r['topic0']==audit.FOLD and int(r['topic3'],16))
    changed['rows'].pop(index)
    with pytest.raises(ValueError,match='pinned RPC'):
        audit.reconstruct(changed,debt)


def test_cash_and_control_are_independently_checked(saved):
    evidence,debt=saved
    changed={**evidence,'capital_cash':[dict(r) for r in evidence['capital_cash']]}
    changed['capital_cash'][0]['cash']=str(D(changed['capital_cash'][0]['cash'])+1)
    with pytest.raises(ValueError,match='saved capital history'):
        audit.reconstruct(changed,debt)
    bad_debt=copy.deepcopy(debt)
    first=next(iter(bad_debt[0]['by_ilk'].values()))
    first['debt']=str(D(first['debt'])+1)
    with pytest.raises(ValueError,match='debt/MSC control'):
        audit.reconstruct(evidence,bad_debt)


def test_duplicate_or_unrelated_vat_logs_are_rejected(saved):
    evidence,debt=saved
    with pytest.raises(ValueError,match='Duplicate'):
        audit.reconstruct({**evidence,'rows':[*evidence['rows'],evidence['rows'][0]]},debt)
    bad=dict(evidence['rows'][0],topic1='0x'+'00'*32)
    with pytest.raises(ValueError,match='Wrong Vat'):
        audit.reconstruct({**evidence,'rows':[bad,*evidence['rows'][1:]]},debt)


def test_fold_indexed_rate_must_match_abi_payload(saved):
    evidence,debt=saved
    rows=list(evidence['rows'])
    index=next(i for i,r in enumerate(rows) if r['topic0']==audit.FOLD)
    rows[index]=dict(rows[index],topic3='0x'+'ff'*32)
    with pytest.raises(ValueError,match='Malformed Fold'):
        audit.reconstruct({**evidence,'rows':rows},debt)
