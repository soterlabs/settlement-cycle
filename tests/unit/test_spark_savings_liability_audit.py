import gzip
import importlib.util
import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location('savings_liability_audit', ROOT/'scripts/audit_spark_savings_liability.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture(scope='module')
def evidence():
    return (
        json.loads(gzip.decompress((ROOT/'tests/fixtures/spark_savings_v2_liability.json.gz').read_bytes())),
        json.loads((ROOT/'reconciliation/spark_savings_funding_inventory_2026_08.json').read_text())['by_venue'],
    )


def test_complete_drip_history_reproduces_pinned_share_and_cash_liabilities(evidence):
    groups, cash = evidence
    results = {g['venue']: audit.audit_vault(g, cash[g['venue']]) for g in groups}
    assert sum(r['events'] for r in results.values()) == 83406
    assert all(not r['non_alm_takes'] for r in results.values())
    assert results['S56']['accrued_vsr'] == '9681325.511299'
    assert results['S57']['accrued_vsr'] == '12262447.192651'
    assert results['S60']['accrued_vsr'] == '2317109.468574'
    # Cash returns exceed takes, but accrued interest leaves positive debt.
    assert results['S59']['net_cash_taken'] == '-11705.263347'
    assert results['S59']['assets_outstanding'] == '370.696577'
    for row in results.values():
        assert D(row['total_assets']) - D(row['idle_cash']) == D(row['assets_outstanding'])
        assert D(row['net_cash_taken']) + D(row['accrued_vsr']) + D(row['share_rounding']) - D(row['other_cash_residual']) == D(row['assets_outstanding'])


def test_missing_deposit_cannot_be_hidden_by_the_final_state(evidence):
    groups, cash = evidence
    g = deepcopy(groups[0])
    deposit = next(r for r in g['rows'] if audit.TOPICS[r['topic0']] == 'deposit')
    g['rows'].remove(deposit)
    with pytest.raises(ValueError, match=r'Drip disagrees|history misses|pinned supply'):
        audit.audit_vault(g, cash[g['venue']])


@pytest.mark.parametrize('field', ['totalSupply()', 'chi()', 'assetsOutstanding()'])
def test_inconsistent_pinned_state_is_rejected(evidence, field):
    groups, cash = evidence
    g = deepcopy(groups[0])
    g['state'][field] += 1
    with pytest.raises(ValueError, match=r'pinned supply|outstanding liability'):
        audit.audit_vault(g, cash[g['venue']])


def test_cash_return_is_not_silently_assumed_to_equal_principal(evidence):
    groups, cash = evidence
    g = groups[2]
    result = audit.audit_vault(g, cash[g['venue']])
    assert result['venue'] == 'S59'
    assert D(result['accrued_vsr']) > 12000
    assert D(result['liability_less_net_cash']) > 12000
