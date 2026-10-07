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
