from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

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
            "daily_sky_rev": "0.011", "base_apr": "0.0365",
        } for i in range(31)],
        sde_daily_breakdown=[],
    )
    result = allocation_financing(pnl, history)
    assert pnl.sky_revenue == D("0.341")
    assert result["allocation_cost_of_funds"] == D("0.31")
    assert result["prime_financing_adjustment"] == D("0.031")
    assert result["allocations"][0]["net_pnl"] == D("0.69")
    assert result["allocations"][0]["borrowed_principal_eom"] == D(100)
    assert (result["allocation_cost_of_funds"] + result["prime_financing_adjustment"]
            == result["existing_cost_of_funds"])


def test_annualization_is_undefined_without_exposure_and_handles_losses():
    assert annualized_yield(D(1), D(0), 31) is None
    assert annualized_yield(D(-100), D(100), 31) is None
    assert annualized_yield(D(5), D(100), 365) == D("0.05")
    assert annualized_yield(D(-5), D(100), 365) == D("-0.05")
