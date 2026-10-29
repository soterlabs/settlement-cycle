import importlib.util
from decimal import Decimal as D
from pathlib import Path

from settle.compute.allocation_financing import annualized_yield

spec = importlib.util.spec_from_file_location(
    "yield_report", Path(__file__).resolve().parents[2] / "scripts/report_allocation_yields.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def inputs(vid="A", cost="1", revenue="5", retained="5", status="calculable_under_assumption"):
    readiness = {
        "results": [
            {
                "prime": "spark",
                "start": "2026-08-01",
                "end": "2026-08-01",
                "allocations": [
                    {"venue": vid, "label": vid, "status": status, "cost_of_funds": cost}
                ],
            }
        ]
    }
    control = {
        "spark": {
            "prime_id": "spark",
            "period": {"start": "2026-08-01", "end": "2026-08-01"},
            "venue_breakdown": [
                {
                    "venue_id": vid,
                    "actual_revenue": revenue,
                    "external_revenue": "0",
                    "revenue": retained,
                    "tw_avg_value_usd": "100",
                }
            ],
        }
    }
    return readiness, control


def test_anchorage_denominator_is_loan_not_escrow_cash():
    readiness, control = inputs(vid="S23")
    control["spark"]["venue_breakdown"][0]["tw_avg_value_usd"] = "1.359103"
    row = module.build(
        readiness, control, {"daily": {"2026-08-01": {"facility_principal": "1000000"}}}
    )["results"][0]["allocations"][0]
    assert D(row["average_exposure"]) == 1000000
    assert D(row["gross_apy"]) == annualized_yield(D(5), D(1000000), 1)


def test_savings_unavailable_cost_does_not_become_zero_net_cost():
    readiness, control = inputs(cost=None, status="savings_split_pending")
    row = module.build(readiness, control, {})["results"][0]["allocations"][0]
    assert row["gross_apy"] is not None
    assert row["net_apy"] is None


def test_net_uses_retained_revenue_after_sde():
    readiness, control = inputs(cost="0", retained="0")
    row = module.build(readiness, control, {})["results"][0]["allocations"][0]
    assert D(row["gross_apy"]) > 0
    assert D(row["net_apy"]) == 0


def test_zero_yield_is_valid_and_sorts_above_negative():
    readiness, control = inputs(revenue="0")
    readiness["results"][0]["allocations"].append(
        {"venue": "B", "label": "B", "status": "calculable_under_assumption", "cost_of_funds": "1"}
    )
    control["spark"]["venue_breakdown"].append(
        {
            "venue_id": "B",
            "actual_revenue": "-1",
            "external_revenue": "0",
            "revenue": "-1",
            "tw_avg_value_usd": "100",
        }
    )
    rows = module.build(readiness, control, {})["results"][0]["allocations"]
    assert [r["venue"] for r in rows] == ["A", "B"]
    assert D(rows[0]["gross_apy"]) == 0


def test_covered_custody_does_not_get_a_second_yield():
    readiness, control = inputs(status="covered_by_boundary")
    row = module.build(readiness, control, {})["results"][0]["allocations"][0]
    assert row["gross_apy"] is None
    assert row["net_apy"] is None
