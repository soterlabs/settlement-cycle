"""Saved-stock diagnostics must conserve debt without silently assigning gaps."""
import importlib.util
from decimal import Decimal as D

import pytest

spec = importlib.util.spec_from_file_location('saved_gap', 'scripts/audit_saved_funding_gap.py')
gap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gap)


def inputs():
    day = '2026-09-01'
    # Outstanding cash debt is 100; five dollars of separate debt is outside
    # this stock. 20 of deductions lower the independent global charge.
    control = {'sky_revenue_daily': [{'date': day, 'cum_debt': '105', 'utilized': '85',
                'daily_sky_rev': '.85', 'alm_usds': '20'}]}
    snapshot = {'identity': {'accounts': ['venue', 'pending']}, 'daily_funding_bridge': {
        day: {'sources': [{'kind': 'sky', 'selected_principal': '80',
            'financing_principal': '5', 'other_principal': '15', 'outstanding_principal': '100'}]}}}
    conditional = {'start': day, 'end': day, 'allocations': [
        {'venue': 'S1', 'conditional_sky_borrowing_cost': '.6', 'daily': {day: {'sky_principal': '70'}}},
        {'venue': 'pending', 'conditional_sky_borrowing_cost': None, 'daily': {day: {'sky_principal': '10'}}}]}
    accounts = {day: {'venue': {'ilk': '70'}, 'pending': {'ilk': '10'},
                'financing:saver-interest:test': {'ilk': '5'}, 'unallocated:tx': {'ilk': '15'}}}
    vat = {'daily': [{'day': day, 'cash_draws_less_repayments': '100'}]}
    return control, snapshot, conditional, accounts, vat


def test_disjoint_components_and_deduction_sign():
    result = gap.decompose(*inputs())
    assert result['components'] == {'financing': D('.05'), 'other': D('.15'),
        'unpriced_selected': D('.1'), 'cash_debt_gap': D(0), 'deduction_difference': D('-.1')}
    assert result['remaining_cost'] == D('.2')  # .85 - .05 other debt - .60 allocations
    assert result['publishable'] is False
    assert result['applied_deductions']['S1'] == D('.1')


def test_missing_account_fails_instead_of_forcing_reconciliation():
    values = inputs()
    del values[3]['2026-09-01']['unallocated:tx']
    with pytest.raises(ValueError, match='other'):
        gap.decompose(*values)


def test_cash_debt_difference_is_explicit_not_reassigned():
    values = inputs()
    values[4]['daily'][0]['cash_draws_less_repayments'] = '110'
    result = gap.decompose(*values)
    assert result['components']['cash_debt_gap'] == D('.1')
    assert result['conditional_allocation_cost'] == D('.6')


def test_missing_independent_debt_day_rejected():
    values = inputs()
    values[4]['daily'] = []
    with pytest.raises(ValueError, match='cash-debt day'):
        gap.decompose(*values)


def test_duplicate_venue_rejected():
    values = inputs()
    values[2]['allocations'].append(values[2]['allocations'][0])
    with pytest.raises(ValueError, match='Duplicate venues'):
        gap.decompose(*values)
