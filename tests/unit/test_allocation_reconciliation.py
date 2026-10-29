from datetime import date
from decimal import Decimal as D

import pytest

from settle.compute.allocation_reconciliation import reconcile_ilks, validate_idle_control


def test_two_ilks_and_msc_deduction_reproduce_unchanged_global_cost():
    control = {'sky_revenue_daily': [dict(date='2026-08-01', cum_debt='120', utilized='100',
                daily_sky_rev='1', base_apr='.04', sde_av='20')]}
    debt = [dict(day='2026-08-01', by_ilk={
        'a': dict(debt='100', prior_msc_debt='10', current_month_msc_debt='0'),
        'b': dict(debt='20', prior_msc_debt='0', current_month_msc_debt='0')})]
    finance = {'daily_allocation_by_ilk': {'2026-08-01': {'0xa': {'cost': '.7'}, '0xb': {'cost': '.2'}}},
               'reconciliation': {'source_complete': True, 'allocation_sum': '.9',
                                  'existing_cost_of_funds': '1'}}
    r = reconcile_ilks(finance, control, debt, {'sde_av': '0xa'})
    assert sum(v['global_cost'] for v in r['by_ilk'].values()) == 1
    assert r['by_ilk']['0xa']['msc_cost'] == D('.1')
    assert all(v['within_one_cent'] for v in r['by_ilk'].values())
    with pytest.raises(ValueError, match='deduction owner'):
        reconcile_ilks(finance, control, debt, {})
    with pytest.raises(ValueError, match='exactly one'):
        reconcile_ilks(finance, control, debt + debt, {'sde_av': '0xa'})
    finance['reconciliation']['allocation_sum'] = '1.1'
    with pytest.raises(ValueError, match='allocation sum'):
        reconcile_ilks(finance, control, debt, {'sde_av': '0xa'})


def test_daily_idle_dollars_must_reproduce_the_published_control():
    control = {'sky_revenue_daily': [{'date': '2026-08-01', 'curve_idle': '20', 'lending_idle': '30'}]}
    day = date(2026, 8, 1)
    validate_idle_control({'V1': {day: D(20)}, 'V2': {day: D(30)}}, control)
    with pytest.raises(ValueError, match='Daily idle'):
        validate_idle_control({'V1': {day: D(20)}}, control)
    control['venue_breakdown'] = [{'venue_id': 'V1'}, {'venue_id': 'V2'}]
    idle = {'V1': {day: D(20)}, 'V2': {day: D(30)}, 'NEW': {day: D(15)}}
    assert validate_idle_control(idle, control) == ['NEW']
    control['venue_breakdown'].append({'venue_id': 'NEW'})
    with pytest.raises(ValueError, match='Daily idle'):
        validate_idle_control(idle, control)


def test_funding_envelopes_use_verified_deduction_owner_without_claiming_reconciliation():
    control = {'sky_revenue_daily': [dict(date='2026-08-01', cum_debt='120', utilized='100',
                daily_sky_rev='1', base_apr='.04', sde_av='20')]}
    debt = [dict(day='2026-08-01', by_ilk={
        'a': dict(debt='100', prior_msc_debt='10', current_month_msc_debt='0'),
        'b': dict(debt='20', prior_msc_debt='0', current_month_msc_debt='0')})]
    finance = {'reconciliation': {'source_complete': False, 'allocation_sum': '0',
                                  'existing_cost_of_funds': '1'},
               'funding_uncertainty': {
                   'joint_cost_by_ilk': {'0xa': {'lower': '-.2', 'upper': '.9'},
                                        '0xb': {'lower': '-.2', 'upper': '.2'}},
                   'daily': {'2026-08-01': {
                       'deduction_totals': {'sde_av': {'lower': 20, 'upper': 20},
                                            'venue_idle': {'lower': 0, 'upper': 0}},
                       'joint_by_ilk': {'0xa': {'principal': {'lower': 0, 'upper': 90}},
                                        '0xb': {'principal': {'lower': 0, 'upper': 20}}}}}}}
    r = reconcile_ilks(finance, control, debt, {'sde_av': '0xa'})['by_ilk']
    assert r['0xa']['diagnostic_cost_bounds'] == {'lower': D('-.2'), 'upper': D('.7')}
    assert r['0xb']['diagnostic_cost_bounds'] == {'lower': D(0), 'upper': D('.2')}
    assert all(v['target_within_bounds_to_one_cent'] for v in r.values())
    assert all(not v['complete'] for v in r.values())
    assert all(v['bounds_use_verified_deduction_owners'] for v in r.values())
