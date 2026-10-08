import importlib.util
import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location('audit_spark_uscc', ROOT / 'scripts/audit_spark_uscc_history.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
FIXTURE = json.loads((ROOT / 'tests/fixtures/spark_uscc_history.json').read_text())


def test_uscc_cash_cost_and_share_units_are_not_interchangeable():
    before = deepcopy(FIXTURE)
    r = audit.inventory(FIXTURE)
    assert FIXTURE == before
    assert r['total_paid'] == D('150010000')
    assert r['total_shares_received'] == r['total_shares_burned'] == D('13265483.981402')
    assert r['ending_token_units'] == 0
    assert all(D(11) < s['implied_cash_per_share'] < D(12) for s in r['subscriptions'])
    assert r['candidate_cash_total'] == D('151013951.26')
    assert all(c['attributed_to_uscc'] is False for c in r['unassigned_cash_candidates'])
    calls = FIXTURE['oracle_calls']
    assert len(calls) == 5 and all(c['method'] == 'superstateOracle()' and int(c['result'], 16) == 0 for c in calls)


@pytest.mark.parametrize('fault', ['duplicate', 'missing_payment', 'missing_burn', 'wrong_holder', 'wrong_order'])
def test_incomplete_evidence_is_not_reported_as_closed_subscription_history(fault):
    f = deepcopy(FIXTURE)
    if fault == 'duplicate':
        f['issuer_transactions'].append(f['issuer_transactions'][0])
    elif fault == 'missing_payment':
        f['issuer_transactions'] = [r for r in f['issuer_transactions'] if r['transaction_hash'] != audit.PAIRS[0][0][0]]
    elif fault == 'missing_burn':
        f['issuer_transactions'] = [r for r in f['issuer_transactions'] if r['transaction_hash'] != '0xcae6ffe1b84fd4702788fa6117b1b3017e6598855408407c10854872c70a856d']
    elif fault == 'wrong_holder':
        f['unassigned_cash_candidates'][0]['topic2'] = '0x' + '0' * 64
    else:
        for r in f['issuer_transactions']:
            if r['transaction_hash'] == audit.PAIRS[0][1]:
                r['block_number'] = 1
    with pytest.raises(ValueError):
        audit.inventory(f)
