import importlib.util
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from settle.normalize.allocation_capital import CapitalBatch, CapitalHistory

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location('financing_bridge', ROOT/'scripts/audit_allocation_financing_bridge.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
ILK, DAY = '0x1234', date(2026, 8, 1)


def inputs():
    h = CapitalHistory((CapitalBatch('draw', DAY, 1, 'ethereum', 1, (), D(100),
                                    minted_by_ilk={ILK: D(100)}),), {'V': 'asset'}, {})
    replay = SimpleNamespace(daily_by_ilk={DAY: {'asset': {ILK: D(80)},
                                              'unallocated:swap': {ILK: D(10)}}})
    finance = {'allocations': [{'venue_id': 'V', 'modeled_cost_of_funds': D(6),
                               'modeled_cost_of_funds_by_ilk': {ILK: D(6)}}],
               'unmatched_receipts': {'unverified': '1'},
               'per_ilk_reconciliation': {
                   'daily': [{'day': str(DAY), 'ilk': ILK, 'deductions': '20'}],
                   'by_ilk': {ILK: {'global_excluding_msc': '8', 'allocation_cost': '0'}}}}
    control = {'venue_breakdown': [{'venue_id': 'V', 'sd_share': '.25'}],
               'sky_revenue_daily': [{'date': str(DAY), 'utilized': '80', 'daily_sky_rev': '8'}]}
    debt = [{'day': str(DAY), 'by_ilk': {ILK: {
        'debt': '100', 'prior_msc_debt': '0', 'current_month_msc_debt': '0'}}}]
    return h, replay, finance, control, debt


def test_bridge_separates_expense_funding_without_certifying_unknown_receipts():
    result = audit.numerical_bridge(*inputs())
    r = result['by_ilk'][ILK]
    assert r['outside_allocation_financing_by_account_type'] == {'unallocated': D(1)}
    assert r['debt_without_remaining_asset_basis_financing'] == D(1)
    assert r['unexplained_numerical_difference'] == 0
    assert r['numerical_bridge_within_one_cent'] is True
    assert r['eligible_allocation_cost'] == '0'
    assert result['unknown_receipts'] == {'unverified': '1'}


def test_wrong_modeled_cost_is_exposed_not_absorbed_as_deduction_difference():
    h, replay, finance, control, debt = inputs()
    finance['allocations'][0]['modeled_cost_of_funds_by_ilk'][ILK] = D(5)
    r = audit.numerical_bridge(h, replay, finance, control, debt)['by_ilk'][ILK]
    assert r['deduction_difference_financing'] == 0
    assert r['modeled_cost_inconsistency'] == D(1)
    assert r['unexplained_numerical_difference'] == D(1)
    assert r['numerical_bridge_within_one_cent'] is False


def test_missing_observed_borrowing_cannot_be_presented_as_realized_loss():
    h, replay, finance, control, debt = inputs()
    debt[0]['by_ilk'][ILK]['debt'] = '101'
    with pytest.raises(ValueError, match='Observed draws'):
        audit.numerical_bridge(h, replay, finance, control, debt)
