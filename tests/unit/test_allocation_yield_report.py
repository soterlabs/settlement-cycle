import importlib.util
from decimal import Decimal as D
from pathlib import Path

import pytest

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


def test_small_negative_yield_is_not_displayed_as_zero():
    assert module.percent("-0.000042") == "-0.0042%"
    assert module.percent("0") == "0.00%"
    assert module.percent(None) == "—"


def test_september_anchorage_never_reuses_the_escrow_or_august_denominator():
    readiness, controls = inputs()
    readiness["results"][0]["start"] = controls["spark"]["period"]["start"] = "2026-09-01"
    readiness["results"][0]["end"] = controls["spark"]["period"]["end"] = "2026-09-30"
    finance = readiness["results"][0]["allocations"][0]
    finance["venue"] = controls["spark"]["venue_breakdown"][0]["venue_id"] = "S23"
    row = module.build(readiness, controls)["results"][0]["allocations"][0]
    assert row["gross_apy"] is None and row["average_exposure"] is None
    assert row["yield_status"] == "loan_exposure_unavailable"
    finance["average_facility_exposure"] = "100000000"
    row = module.build(readiness, controls)["results"][0]["allocations"][0]
    assert row["average_exposure"] == "100000000"
    assert row["gross_apy"] is not None


def test_nft_uses_measured_average_not_larger_fixed_notional():
    readiness, controls = inputs(vid="NFT")
    controls["spark"]["venue_breakdown"][0]["tw_avg_notional_usd"] = "250"
    row = module.build(readiness, controls)["results"][0]["allocations"][0]
    assert D(row["average_exposure"]) == 100


def test_facility_uses_updated_outstanding_claim_not_stale_configured_notional():
    readiness, controls = inputs(vid="E42")
    finance = readiness["results"][0]["allocations"][0]
    finance["average_facility_exposure"] = "349916701"
    revenue = controls["spark"]["venue_breakdown"][0]
    revenue.update(tw_avg_value_usd="0", tw_avg_notional_usd="304000001")
    row = module.build(readiness, controls)["results"][0]["allocations"][0]
    assert D(row["average_exposure"]) == 349916701


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity"])
def test_invalid_measured_value_cannot_be_hidden_by_positive_notional(value):
    readiness, controls = inputs()
    controls["spark"]["venue_breakdown"][0].update(
        tw_avg_value_usd=value, tw_avg_notional_usd="100")
    with pytest.raises(ValueError, match="Invalid measured yield exposure"):
        module.build(readiness, controls)
