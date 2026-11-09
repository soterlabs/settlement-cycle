"""Custody rows and credits conserve costs without inventing funding or yield."""
from decimal import Decimal as D

import pytest

from settle.compute.allocation_cost_rows import (
    excess_deduction_credit,
    psm_principal_inventory,
)

A = 'psm:arbitrum:0xpool:0xholder'
B = 'psm:base:0xpool:0xholder'


def test_psm_inventory_has_no_cost_or_yield_and_is_not_an_allocation():
    snapshots = {'day1': {A: {'ilk1': '100', 'ilk2': '50'}, B: {'ilk1': '10'}},
                 'day2': {A: {'ilk1': '80', 'ilk2': '40'}, B: {'ilk1': '20'}}}
    rows = psm_principal_inventory(snapshots, ['day1', 'day2'])
    assert len(rows) == 3
    assert sum(r['average_sky_principal'] for r in rows) == D(150)
    assert all(r['is_allocation'] is False for r in rows)
    assert all(not any('cost' in k or 'yield' in k for k in r) for r in rows)


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
    with pytest.raises(ValueError, match='cannot be included'):
        psm_principal_inventory({'d': {A: {'ilk': '1'}}}, {'d': D('.01')}, selected_accounts=[A])


@pytest.mark.parametrize('bad', ['-1', 'NaN', 'Infinity'])
def test_invalid_funding_never_becomes_a_credit(bad):
    with pytest.raises(ValueError, match='finite nonnegative'):
        psm_principal_inventory({'d': {A: {'ilk': bad}}}, {'d': D('.01')})


def test_missing_dates_are_not_silently_zeroed():
    with pytest.raises(ValueError, match='Missing daily'):
        psm_principal_inventory({}, {'d': D('.01')})
    with pytest.raises(ValueError, match='days must match'):
        excess_deduction_credit('x', {}, {'d': 1}, {'d': D('.01')}, scope='venue')
