from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from settle.compute.allocation_financing import allocation_financing, annualized_yield
from settle.domain.monthly_pnl import VenueRevenue
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory


def test_venue_cost_and_adjustment_reconcile_without_changing_settlement():
    start, end = date(2026, 8, 1), date(2026, 8, 31)
    history = CapitalHistory((CapitalBatch(
        "draw", date(2026, 7, 1), 1, "ethereum", 1,
        (AssetMovement("asset", D(0), D(100)),), D(100),
    ),), {"V1": "asset"}, {})
    pnl = SimpleNamespace(
        period=SimpleNamespace(start=start, end=end),
        sky_revenue=D("0.341"), sde_revenue=D(0), susds_spread_reimbursement=D(0),
        venue_breakdown=[VenueRevenue("V1", "Venue", D(100), D(101), D(0), D(1),
                                     actual_revenue=D(1), tw_avg_value=D(100))],
        sky_revenue_daily=[{
            "date": (start + timedelta(days=i)).isoformat(), "utilized": "110",
            "daily_sky_rev": "0.011", "base_apr": "0.0365", "cum_debt": "110",
        } for i in range(31)],
        sde_daily_breakdown=[],
    )
    result = allocation_financing(pnl, history)
    assert pnl.sky_revenue == D("0.341")
    assert result["allocation_cost_of_funds"] == D("0.31")
    assert result["prime_financing_adjustment"] == D("0.031")
    assert result["reconciliation"]["source_complete"] is True
    assert result["reconciliation"]["complete"] is False
    assert sum((r['unattributed_debt_cost'] + r['deduction_difference_cost']
                for r in result['reconciliation']['daily_basis_controls']), D(0)) == D('0.031')
    assert result["allocations"][0]["net_pnl"] == D("0.69")
    assert result["allocations"][0]["borrowed_principal_eom"] == D(100)
    assert (result["allocation_cost_of_funds"] + result["prime_financing_adjustment"]
            == result["existing_cost_of_funds"])


def test_annualization_is_undefined_without_exposure_and_handles_losses():
    assert annualized_yield(D(1), D(0), 31) is None
    assert annualized_yield(D(-100), D(100), 31) is None
    assert annualized_yield(D(5), D(100), 365) == D("0.05")
    assert annualized_yield(D(-5), D(100), 365) == D("-0.05")


def test_daily_idle_dollars_follow_reallocation_instead_of_period_average():
    start, end = date(2026, 8, 1), date(2026, 8, 2)
    history = CapitalHistory((
        CapitalBatch('draw', start, 1, 'ethereum', 1,
                     (AssetMovement('asset', D(0), D(100)),), D(100)),
        CapitalBatch('withdraw', end, 2, 'ethereum', 2,
                     (AssetMovement('asset', D(100), D(-50)),
                      AssetMovement('cash', D(0), D(50)))),
    ), {'V1': 'asset'}, {})
    pnl = SimpleNamespace(
        period=SimpleNamespace(start=start, end=end),
        sky_revenue=D('0.015'), sde_revenue=D(0), susds_spread_reimbursement=D(0),
        venue_breakdown=[VenueRevenue('V1', 'Venue', D(100), D(50), D(-50), D(0),
                                     tw_avg_value=D(75), lending_idle_tw_avg_usd=D(25))],
        sky_revenue_daily=[{'date': day.isoformat(), 'utilized': '100',
                            'daily_sky_rev': '0.01', 'base_apr': '0.0365'}
                           for day in (start, end)], sde_daily_breakdown=[],
    )
    result = allocation_financing(pnl, history, idle_amounts={'V1': {start: D(0), end: D(50)}})
    assert result['allocations'][0]['cost_of_funds'] == D('0.01')
    missing_daily = allocation_financing(pnl, history)
    assert missing_daily['allocations'][0]['cost_of_funds'] is None


def test_reconciliation_does_not_pass_by_inserting_a_financing_residual():
    # The fixture deliberately omits every allocation: the residual could
    # equal global cost, but that cannot make the allocation sum reconcile.
    start = date(2026, 8, 1)
    pnl = SimpleNamespace(period=SimpleNamespace(start=start, end=start),
        sky_revenue=D(12), sde_revenue=D(0), susds_spread_reimbursement=D(0),
        venue_breakdown=[], sky_revenue_daily=[{'date': start.isoformat(),
            'utilized': '100', 'daily_sky_rev': '12', 'base_apr': '0.04'}],
        sde_daily_breakdown=[])
    result = allocation_financing(pnl, CapitalHistory((), {}, {}))
    assert result['prime_financing_adjustment'] == D(12)
    assert result['reconciliation']['difference'] == D(-12)
    assert result['reconciliation']['within_one_cent'] is False


def test_idle_exemption_uses_asset_dollars_including_earned_value():
    day = date(2026, 8, 1)
    history = CapitalHistory((CapitalBatch('draw', day, 1, 'ethereum', 1,
        (AssetMovement('asset', D(0), D(100)),), D(100)),), {'V1': 'asset'}, {})
    pnl = SimpleNamespace(period=SimpleNamespace(start=day, end=day),
        sky_revenue=D('0.0045'), sde_revenue=D(0), susds_spread_reimbursement=D(0),
        venue_breakdown=[VenueRevenue('V1', 'Venue', D(100), D(110), D(0), D(10),
            actual_revenue=D(10), tw_avg_value=D(110), lending_idle_tw_avg_usd=D(55))],
        sky_revenue_daily=[{'date': str(day), 'utilized': '45', 'daily_sky_rev': '0.0045',
                            'base_apr': '0.0365'}], sde_daily_breakdown=[])
    result = allocation_financing(pnl, history, idle_amounts={'V1': {day: D(55)}})
    assert result['allocations'][0]['borrowed_principal_eom'] == D(100)
    assert result['allocations'][0]['cost_of_funds'] == D('0.0045')
    assert result['reconciliation']['within_one_cent'] is True


def test_mixed_ilk_deductions_and_tracing_only_allocation_do_not_invent_revenue():
    day = date(2026, 8, 1)
    history = CapitalHistory((CapitalBatch('draw', day, 1, 'ethereum', 1,
        (AssetMovement('asset', D(0), D(100)),), D(100),
        minted_by_ilk={'A': D(60), 'B': D(40)}),), {'PAU': 'asset'}, {},
        analytics_only_venues=('PAU',))
    pnl = SimpleNamespace(period=SimpleNamespace(start=day, end=day),
        sky_revenue=D('.008'), sde_revenue=D(0), susds_spread_reimbursement=D(0),
        venue_breakdown=[], sde_daily_breakdown=[],
        sky_revenue_daily=[{'date': str(day), 'utilized': '80', 'daily_sky_rev': '.008',
                           'base_apr': '.0365'}])
    result = allocation_financing(pnl, history, idle_amounts={'PAU': {day: D(20)}})
    row = result['allocations'][0]
    assert row['cost_of_funds_by_ilk'] == {'A': D('.0048'), 'B': D('.0032')}
    assert sum(row['cost_of_funds_by_ilk'].values()) == row['cost_of_funds']
    assert row['net_pnl'] is None and row['net_apy'] is None and row['gross_apy'] is None
    assert row['revenue_available'] is False
    assert pnl.venue_breakdown == []


@pytest.mark.parametrize('idle, expected', [(D(0), D('.01')), (D(40), D('.006'))])
def test_funded_nft_omitted_from_old_revenue_snapshot_remains_visible_but_uncertified(idle, expected):
    day = date(2026, 8, 1)
    history = CapitalHistory((CapitalBatch('draw', day, 1, 'ethereum', 1,
        (AssetMovement('nft:position', D(0), D(100)),), D(100),
        minted_by_ilk={'A': D(100)}),),
        {'NFT': 'nft:aggregate', 'EMPTY': 'unused'}, {},
        custody_accounts={'NFT': ['nft:position']})
    pnl = SimpleNamespace(period=SimpleNamespace(start=day, end=day),
        sky_revenue=D('.01'), sde_revenue=D(0), susds_spread_reimbursement=D(0),
        venue_breakdown=[], sde_daily_breakdown=[],
        sky_revenue_daily=[{'date': str(day), 'utilized': '100', 'daily_sky_rev': '.01',
                           'base_apr': '.0365'}])
    result = allocation_financing(pnl, history, idle_amounts={'NFT': {day: idle}})
    assert result['unreported_allocation_ids'] == ['NFT']
    row, = result['allocations']
    assert row['venue_id'] == 'NFT'
    assert row['borrowed_principal_average'] == 100
    assert row['modeled_cost_of_funds_by_ilk'] == {'A': expected}
    assert row['cost_of_funds'] is None
    assert row['net_pnl'] is None and row['net_apy'] is None and row['gross_apy'] is None
    assert row['revenue_available'] is False
    assert result['allocation_cost_of_funds'] == 0
    assert result['existing_cost_of_funds'] == D('.01')
    assert pnl.venue_breakdown == [] and pnl.sky_revenue == D('.01')


def test_reported_nft_uses_its_actual_deductions_and_revenue_without_a_duplicate_row():
    day = date(2026, 8, 1)
    history = CapitalHistory((CapitalBatch('draw', day, 1, 'ethereum', 1,
        (AssetMovement('nft:position', D(0), D(100)),), D(100),
        minted_by_ilk={'A': D(100)}),), {'NFT': 'nft:aggregate'}, {},
        custody_accounts={'NFT': ['nft:position']})
    pnl = SimpleNamespace(period=SimpleNamespace(start=day, end=day),
        sky_revenue=D('.006'), sde_revenue=D(0), susds_spread_reimbursement=D(0),
        venue_breakdown=[VenueRevenue('NFT', 'Pool', D(100), D(101), D(0), D(1),
            actual_revenue=D(1), tw_avg_value=D(100), amm_idle_usds_tw_avg_usd=D(40))],
        sde_daily_breakdown=[],
        sky_revenue_daily=[{'date': str(day), 'utilized': '60', 'daily_sky_rev': '.006',
                           'base_apr': '.0365'}])
    result = allocation_financing(pnl, history, idle_amounts={'NFT': {day: D(40)}})
    assert result['unreported_allocation_ids'] == []
    row, = result['allocations']
    assert row['cost_of_funds'] == D('.006')
    assert row['net_pnl'] == D('.994')
    assert row['gross_apy'] is not None and row['net_apy'] is not None
    assert row['revenue_available'] is True
