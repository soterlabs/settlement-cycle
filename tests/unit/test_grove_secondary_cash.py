from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from settle.compute.allocation_financing import allocation_financing
from settle.compute.grove_secondary_cash import (
    HOLDER,
    PRIMARY_CASH,
    TOKENS,
    include_grove_secondary_cash,
)
from settle.domain.monthly_pnl import VenueRevenue
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

CASH = f'ethereum:{HOLDER}:{TOKENS["E15_PAU_CASH"]}'
START, END = date(2026, 8, 19), date(2026, 8, 20)
ILK = '0x414c4c4f4341544f522d47524f56452d41000000000000000000000000000000'


def history():
    return CapitalHistory((
        CapitalBatch('draw', START, 1, 'ethereum', 1,
            (AssetMovement(CASH, D(0), D('1000000')),), D('1000000'), minted_by_ilk={ILK: D('1000000')}),
        CapitalBatch('earn', END, 2, 'ethereum', 2,
            (AssetMovement(CASH, D('1000000'), D('50000'), external_income=D('50000')),)),
        CapitalBatch('invest', END, 3, 'ethereum', 3,
            (AssetMovement(CASH, D('1050000'), D('-400000')),
             AssetMovement('investment', D(0), D('400000')))),
    ), {'E15': PRIMARY_CASH, 'V': 'investment'}, {})


def test_mapping_adds_only_observed_cash_and_does_not_change_events():
    h = history()
    linked = include_grove_secondary_cash(h)
    assert linked.batches == h.batches and linked.idle_accounts == h.idle_accounts
    assert linked.venue_accounts['E15_PAU_CASH'] == CASH
    assert linked.analytics_only_venues == ('E15_PAU_CASH',)
    assert linked == include_grove_secondary_cash(linked)


def test_secondary_cash_and_investment_costs_sum_to_unchanged_ilk_control():
    h = history()
    pnl = SimpleNamespace(period=SimpleNamespace(start=START, end=END),
        sky_revenue=D(200), sde_revenue=D(0), susds_spread_reimbursement=D(0),
        venue_breakdown=[VenueRevenue('E15', 'Cash', D(0), D(0), D(0), D(0)),
                         VenueRevenue('V', 'Investment', D(0), D('400000'), D('400000'), D(0))],
        sky_revenue_daily=[{'date': d.isoformat(), 'utilized': '1000000', 'cum_debt': '1000000',
                            'daily_sky_rev': '100', 'base_apr': '.0365'} for d in (START, END)],
        sde_daily_breakdown=[])
    r = allocation_financing(pnl, h, quantify_uncertainty=True)
    assert abs(r['allocation_cost_of_funds'] - D(200)) < D('1e-20')
    assert r['reconciliation']['within_one_cent'] is True
    assert pnl.sky_revenue == D(200) and len(pnl.venue_breakdown) == 2
    cash = next(a for a in r['allocations'] if a['venue_id'] == 'E15_PAU_CASH')
    assert cash['revenue_available'] is False and cash['net_pnl'] is None
    assert cash['gross_apy'] is None
    assert cash['borrowed_principal_eom'] < D('650000')  # $50k earnings never become borrowed.
    assert cash['cost_of_funds_by_ilk'][ILK] == cash['cost_of_funds']


def test_already_owned_cash_is_not_counted_twice():
    h = history()
    configured = replace(h, venue_accounts={**h.venue_accounts, 'Configured cash': CASH})
    assert include_grove_secondary_cash(configured) == configured
    custody = replace(h, custody_accounts={'V': [CASH]})
    assert include_grove_secondary_cash(custody) == custody


def test_unrelated_holder_and_unobserved_token_are_not_added():
    h = history()
    unknown = 'ethereum:0x' + '99' * 20 + ':' + TOKENS['E15_PAU_CASH']
    h = replace(h, batches=(replace(h.batches[0], movements=(AssetMovement(unknown, D(0), D(10)),)),))
    assert include_grove_secondary_cash(h) == h


def test_conflicting_venue_identifier_fails():
    h = history()
    h = replace(h, venue_accounts={**h.venue_accounts, 'E15_PAU_CASH': 'different'})
    with pytest.raises(ValueError, match='another account'):
        include_grove_secondary_cash(h)


def test_display_only_primary_ausd_keeps_borrowing_cost_but_no_revenue():
    ausd = PRIMARY_CASH.rsplit(':', 1)[0] + ':' + TOKENS['E14_PAU_CASH']
    h = history()
    h = replace(h, batches=tuple(replace(b, movements=tuple(
        replace(m, account=ausd) if m.account == CASH else m for m in b.movements))
        for b in h.batches), venue_accounts={**h.venue_accounts, 'E14': ausd})
    pnl = SimpleNamespace(period=SimpleNamespace(start=START, end=END),
        sky_revenue=D(200), sde_revenue=D(0), susds_spread_reimbursement=D(0),
        venue_breakdown=[VenueRevenue('V', 'Investment', D(0), D('400000'), D('400000'), D(0))],
        sky_revenue_daily=[{'date': d.isoformat(), 'utilized': '1000000',
                            'daily_sky_rev': '100', 'base_apr': '.0365'} for d in (START, END)],
        sde_daily_breakdown=[])
    r = allocation_financing(pnl, h)
    row = next(a for a in r['allocations'] if a['venue_id'] == 'E14')
    assert row['cost_of_funds'] > 0
    assert abs(r['allocation_cost_of_funds']-D(200)) < D('1e-20')
    assert row['revenue_available'] is False
    assert row['gross_apy'] is None and row['net_pnl'] is None
    assert row['borrowed_principal_eom'] < D('650000')  # Earnings stay unborrowed.
    assert pnl.sky_revenue == D(200) and len(pnl.venue_breakdown) == 1
    # If a caller does include E14 in its revenue scope, still emit only one row.
    pnl.venue_breakdown.append(VenueRevenue('E14', 'AUSD', D(0), D(0), D(0), D(0)))
    again = allocation_financing(pnl, h)
    assert sum(a['venue_id'] == 'E14' for a in again['allocations']) == 1
    assert again['allocation_cost_of_funds'] == r['allocation_cost_of_funds']
