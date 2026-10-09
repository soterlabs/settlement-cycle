import importlib.util
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('outflow_costs', Path(__file__).parents[2]/'scripts/audit_unallocated_borrowing_costs.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def inputs():
    first, second = date(2026, 8, 1), date(2026, 8, 2)
    daily = {first: {'unallocated:ethereum:tx': {'ilkA': D(10), 'ilkB': D(20)},
                     'live-allocation': {'ilkA': D(999)}},
             second: {'unallocated:ethereum:tx': {'ilkA': D(4), 'ilkB': D(6)}}}
    control = {'sky_revenue_daily': [{'date': str(first), 'utilized': '100', 'daily_sky_rev': '1'},
                                     {'date': str(second), 'utilized': '100', 'daily_sky_rev': '2'}]}
    outflows = {'ethereum:tx': '50', 'ethereum:own-funds': '900000'}
    proof = {'rows': [{'identity': 'ethereum:tx', 'observed_outflow': '50',
                       'witnessed_components_signed_gain': {'curve': '-49.999'},
                       'remaining_signed_difference': '.001'}]}
    return daily, control, outflows, proof


def test_cost_uses_each_days_actual_sky_basis_and_rate_not_historical_gross_value():
    result = audit.summarize(*inputs())
    assert result['monthly_sky_cost_by_ilk'] == {'ilkA': '0.18', 'ilkB': '0.32'}
    row, gift = result['records']
    assert row['average_sky_principal_by_ilk'] == {'ilkA': '7', 'ilkB': '13'}
    assert row['closing_sky_principal_by_ilk'] == {'ilkA': '4', 'ilkB': '6'}
    assert row['evidence_status'] == 'execution cost: sub-cent remainder'
    assert row['execution_remaining_difference'] == '.001'
    assert gift['monthly_sky_cost_by_ilk'] == {}
    assert gift['historical_outflow_value'] == '900000'


def test_stale_proof_does_not_certify_an_outflow():
    daily, control, outflows, proof = inputs()
    proof['rows'][0]['observed_outflow'] = '49'
    result = audit.summarize(daily, control, outflows, proof)
    assert result['records'][0]['evidence_status'] == 'no matching execution witness'
    assert result['records'][0]['execution_remaining_difference'] is None
    assert result['monthly_sky_cost_by_ilk'] == {'ilkA': '0.18', 'ilkB': '0.32'}


def test_missing_daily_snapshot_or_missing_transaction_evidence_fails():
    daily, control, outflows, proof = inputs()
    daily.pop(date(2026, 8, 2))
    with pytest.raises(ValueError, match='daily principal'):
        audit.summarize(daily, control, outflows, proof)
    daily, control, outflows, proof = inputs()
    outflows.pop('ethereum:tx')
    with pytest.raises(ValueError, match='transaction evidence'):
        audit.summarize(daily, control, outflows, proof)
