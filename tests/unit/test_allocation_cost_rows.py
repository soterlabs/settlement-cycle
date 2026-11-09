"""Custody rows and credits conserve costs without inventing funding or yield."""
from decimal import Decimal as D

import pytest

from settle.compute.allocation_cost_rows import (
    deduction_credit,
    excess_deduction_credit,
    psm_cost_rows,
)

A = 'psm:arbitrum:0xpool:0xholder'
B = 'psm:base:0xpool:0xholder'


def test_psm_rows_keep_chains_and_ilks_separate_and_shared_credits_unassigned():
    rates = {'day1': D('.01'), 'day2': D('.02')}
    snapshots = {'day1': {A: {'ilk1': '100', 'ilk2': '50'}, B: {'ilk1': '10'}},
                 'day2': {A: {'ilk1': '80', 'ilk2': '40'}, B: {'ilk1': '20'}}}
    rows = psm_cost_rows(snapshots, rates)
    assert len(rows) == 3
    assert sum(r['cost_of_funds'] for r in rows) == D('4.4')
    assert all(r['net_cost_after_shared_deductions'] is None for r in rows)
    credit = deduction_credit('psm_usds', {'day1': 30, 'day2': 20}, rates, scope='psm_all_chains')
    assert credit['cost_of_funds'] == D('-.7')
    assert sum(r['cost_of_funds'] for r in rows) + credit['cost_of_funds'] == D('3.7')


def test_excess_credit_preserves_zero_floor_and_does_not_apply_deduction_twice():
    principal = {'day1': D(100), 'day2': D(0)}
    deduction = {'day1': D(120), 'day2': D(30)}
    rates = {'day1': D('.01'), 'day2': D('.02')}
    credit = excess_deduction_credit('excess', principal, deduction, rates, scope='S62')
    assert credit['daily_cost'] == {'day1': D('-.2'), 'day2': D('-.6')}
    capped_cost = sum(max(D(0), principal[d] - deduction[d]) * rates[d] for d in rates)
    assert capped_cost == 0
    assert capped_cost + credit['cost_of_funds'] == sum((principal[d]-deduction[d])*rates[d] for d in rates)
    assert principal == {'day1': D(100), 'day2': D(0)}


def test_deduction_below_principal_has_no_additional_credit():
    row = excess_deduction_credit('excess', {'d': 100}, {'d': 30}, {'d': D('.01')}, scope='venue')
    assert row['cost_of_funds'] == 0


def test_psm_already_selected_cannot_be_added_again():
    with pytest.raises(ValueError, match='already included'):
        psm_cost_rows({'d': {A: {'ilk': '1'}}}, {'d': D('.01')}, selected_accounts=[A])


@pytest.mark.parametrize('bad', ['-1', 'NaN', 'Infinity'])
def test_invalid_funding_never_becomes_a_credit(bad):
    with pytest.raises(ValueError, match='finite nonnegative'):
        psm_cost_rows({'d': {A: {'ilk': bad}}}, {'d': D('.01')})


def test_missing_dates_are_not_silently_zeroed():
    with pytest.raises(ValueError, match='Missing daily'):
        psm_cost_rows({}, {'d': D('.01')})
    with pytest.raises(ValueError, match='days must match'):
        excess_deduction_credit('x', {}, {'d': 1}, {'d': D('.01')}, scope='venue')
