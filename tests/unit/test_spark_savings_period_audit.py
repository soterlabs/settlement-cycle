import gzip
import importlib.util
import json
import sys
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope='module')
def period_evidence():
    # The audited scripts are standalone CLI modules with sibling imports.
    sys.path.insert(0, str(ROOT/'scripts'))
    try:
        spec = importlib.util.spec_from_file_location('savings_period_audit', ROOT/'scripts/audit_spark_savings_period.py')
        audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit)
    finally:
        sys.path.pop(0)
    def read(name):
        path = ROOT/'tests/fixtures'/name
        raw = path.read_bytes()
        return json.loads(gzip.decompress(raw) if name.endswith('.gz') else raw)
    return audit, read('spark_savings_v2_liability.json.gz'), read('spark_savings_v2_funding.json.gz'), read('spark_savings_v2_august_opening.json')


def test_august_accrual_reconciles_both_boundaries_and_includes_un_dripped_tails(period_evidence):
    audit, groups, cash, opening = period_evidence
    rows = audit.period_accrual(groups, cash, opening)
    assert sum(D(r['period_change']['accrued_vsr']) for r in rows) == D('1762542.010973')
    pyusd = next(r for r in rows if r['venue'] == 'S59')['period_change']
    assert D(pyusd['net_cash_taken']) == 0
    assert D(pyusd['assets_outstanding']) == D('267.696756')
    assert D(pyusd['accrued_vsr']) == D('267.696763')
    for row in rows:
        change = row['period_change']
        assert D(change['net_cash_taken']) + D(change['accrued_vsr']) + D(change['share_rounding']) - D(change['other_cash_residual']) == D(change['assets_outstanding'])


def test_period_rejects_mismatched_cash_cutoff(period_evidence):
    audit, groups, cash, opening = period_evidence
    cash = deepcopy(cash)
    cash[0]['pin'] -= 1
    with pytest.raises(ValueError, match='closing cash and liability pins'):
        audit.period_accrual(groups, cash, opening)


def test_period_rejects_different_opening_vault(period_evidence):
    audit, groups, cash, opening = period_evidence
    opening = deepcopy(opening)
    opening[0]['vault'] = opening[1]['vault']
    with pytest.raises(ValueError, match='opening boundary'):
        audit.period_accrual(groups, cash, opening)
