import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location('lp_audit', ROOT/'scripts/audit_grove_lp_residuals.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
EVENTS = json.loads((ROOT/'tests/fixtures/grove_curve_lp_residual_events.json').read_text())
RESIDUALS = json.loads((ROOT/'reconciliation/grove_swap_execution_shortfalls_2026_08.json').read_text())['remaining_residuals']


def test_actual_lp_cash_and_method_b_price_explain_both_residuals():
    result = audit.identify(EVENTS, RESIDUALS)
    assert len(result) == 2
    assert result[0]['cash'] == '24998000'
    assert result[1]['cash'] == '5002074.106834'
    assert {r['capital_batch'] for r in result} == set(RESIDUALS)
    for r in result:
        assert abs(audit.D(r['normalization_rounding_difference'])) < audit.D('1e-18')


def test_missing_cash_or_wrong_historical_state_cannot_explain_lp_gap():
    broken = deepcopy(EVENTS)
    broken[0]['balances'][0] = '0'
    with pytest.raises(ValueError, match='do not explain'):
        audit.identify(broken, RESIDUALS)
    broken = deepcopy(EVENTS)
    broken[1]['rows'] = [r for r in broken[1]['rows'] if r['address'] not in audit.COINS]
    with pytest.raises(ValueError, match='do not explain'):
        audit.identify(broken, RESIDUALS)
    broken = deepcopy(EVENTS)
    broken[0]['block'] += 1
    with pytest.raises(ValueError, match='pinned'):
        audit.identify(broken, RESIDUALS)
