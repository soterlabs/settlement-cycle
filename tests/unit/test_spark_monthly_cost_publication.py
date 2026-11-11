import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("scripts").resolve()))
from prepare_spark_monthly_cost_publication import prepare, publish_reviewed


def inputs():
    costs = {
        "prime": "spark",
        "month": "2026-09",
        "venue_cost_subtotal_usd": "2",
        "limitations": ["PSM gap deferred"],
        "allocations": [
            {
                "venue": "S1",
                "label": "USDS",
                "borrowing_cost_usd": "2",
                "status": "working_estimate_existing_funding_policy",
                "average_sky_funded_principal_usd": "50",
                "average_savings_funded_principal_usd": "40",
            }
        ],
    }
    readiness = {
        "results": [
            {
                "prime": "spark",
                "start": "2026-09-01",
                "end": "2026-09-30",
                "method": "original capital",
                "policy": "old",
                "limitations": [],
                "allocations": [
                    {
                        "venue": "S1",
                        "label": "USDS",
                        "status": "savings_split_pending",
                        "cost_of_funds": None,
                        "average_principal": "90",
                        "closing_principal": "90",
                    }
                ],
            }
        ]
    }
    control = {
        "prime_id": "spark",
        "period": {"start": "2026-09-01", "end": "2026-09-30"},
        "venue_breakdown": [
            {
                "venue_id": "S1",
                "actual_revenue": "5",
                "external_revenue": "0",
                "revenue": "4",
                "tw_avg_value_usd": "100",
            }
        ],
    }
    return costs, readiness, control


def test_updates_costs_and_net_returns_without_changing_revenue_or_nav():
    c, r, p = inputs()
    before = copy.deepcopy((c, r, p))
    doc = prepare(c, r, p, hashes={"inputs": "a" * 64})
    row = doc["allocations"][0]
    assert (c, r, p) == before
    assert row["borrowing_cost_usd"] == "2"
    assert row["gross_revenue_usd"] == "5"
    assert row["prime_retained_revenue_usd"] == "4"
    assert row["net_pnl_usd"] == "2"
    assert row["average_original_principal_usd"] == "90"
    assert row["average_yield_exposure_usd"] == "100"
    assert row["average_sky_funded_principal_usd"] == "50"
    assert row["borrowing_cost_method"] == "proportional_sky_savings"


def test_absent_revenue_stays_null_and_pre_september_is_rejected():
    c, r, p = inputs()
    p["venue_breakdown"] = []
    row = prepare(c, r, p, hashes={"inputs": "a" * 64})["allocations"][0]
    assert row["borrowing_cost_usd"] == "2"
    assert row["gross_apy"] is None and row["net_pnl_usd"] is None
    c["month"] = "2026-08"
    with pytest.raises(ValueError, match="September"):
        prepare(c, r, p, hashes={"inputs": "a" * 64})


def test_publication_refuses_changed_baseline(monkeypatch):
    import prepare_spark_monthly_cost_publication as mod

    class Conn:
        def execute(self, *args):
            pass

    monkeypatch.setattr(mod, "read", lambda *a: {"revision_id": "new"})
    with pytest.raises(ValueError, match="changed"):
        publish_reviewed(Conn(), {"prime": "spark", "month": "2026-09"}, "missing")


def test_export_cannot_redistribute_costs_while_preserving_total():
    from prepare_spark_monthly_cost_publication import verify_estimates

    c, _, _ = inputs()
    evidence = {
        "start": "2026-09-01",
        "end": "2026-09-30",
        "allocations": [
            {"venue": "S1", "conditional_sky_borrowing_cost": "1"},
            {"venue": "S2", "conditional_sky_borrowing_cost": "1"},
        ],
    }
    c["allocations"].append({**c["allocations"][0], "venue": "S2", "borrowing_cost_usd": "0"})
    with pytest.raises(ValueError, match="differs from reviewed"):
        verify_estimates(c, evidence)
    c["allocations"][0]["borrowing_cost_usd"] = "1"
    c["allocations"][1]["borrowing_cost_usd"] = "1"
    verify_estimates(c, evidence)
