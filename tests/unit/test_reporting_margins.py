from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from settle.revenue.financial import metric
from settle.revenue.margins import allocation_slice, decode, project, ratio
from settle.revenue.reporting_expenses import monthly, snapshot


def statement():
    periods = [
        dict(
            key=f"2026-{i:02}",
            state="complete" if i < 10 else "future",
            expected_cutoff=date(2026, i + 1, 1)
            .fromordinal(date(2026, i + 1, 1).toordinal() - 1)
            .isoformat()
            if i < 12
            else "2026-12-31",
        )
        for i in range(1, 13)
    ]
    return dict(
        prime="spark",
        year=2026,
        review_policy={"enabled": True},
        as_of="2026-09-30",
        selection="statement-selection",
        report_hash="f" * 64,
        source_manifest={},
        periods=periods,
        lines=[
            dict(id=k, values={p["key"]: metric(v) for p in periods})
            for k, v in {
                "statement.interest": "1000",
                "statement.funding": "300",
                "statement.net_interest": "700",
                "statement.net_revenue": "1100",
                "statement.noninterest_income": "400",
                "expenses.compensation.spk": "17",
            }.items()
        ],
    )


def publication():
    return dict(
        prime="spark",
        month="2026-09",
        period={"end": "2026-09-30"},
        revision_id="a" * 64,
        reconciliation={"comparison_difference_after_financing_usd": "126444"},
        allocations=[
            dict(
                venue_id="S1",
                average_yield_exposure_usd="100",
                prime_retained_revenue_usd="10",
                borrowing_cost_usd="3",
            ),
            dict(
                venue_id="S2",
                average_yield_exposure_usd="900",
                prime_retained_revenue_usd="18",
                borrowing_cost_usd="9",
            ),
            dict(
                venue_id="S56",
                average_yield_exposure_usd=None,
                prime_retained_revenue_usd=None,
                borrowing_cost_usd=None,
            ),
        ],
    )


def test_weight_amounts_not_apy_and_do_not_redistribute_gap():
    r = allocation_slice(publication(), "2026-09")
    assert Decimal(r["metrics"]["yield_"]["value"]) == Decimal(".028")
    assert Decimal(r["metrics"]["nim"]["value"]) == Decimal(".016")
    assert r["status"] == "partial"
    assert r["reconciliation"]["comparison_difference_after_financing_usd"] == "126444"
    assert r["excluded_venues"][0]["venue"] == "S56"


def test_duplicate_period_and_invalid_amount_rejected():
    p = publication()
    with pytest.raises(ValueError):
        allocation_slice(p, "2026-08")
    p["allocations"].append(p["allocations"][0])
    with pytest.raises(ValueError):
        allocation_slice(p, "2026-09")
    p = publication()
    p["allocations"][0]["average_yield_exposure_usd"] = "NaN"
    with pytest.raises(ValueError):
        allocation_slice(p, "2026-09")


def test_partial_denominator_never_becomes_complete_margin():
    s = statement()
    s["review_policy"] = {"enabled": True}
    source = snapshot("spark", 2026)
    a = [None] * 12
    a[8] = publication()
    result = project(s, a, source)
    m = result["months"][8]
    assert m["numerators"]["funding_expense"]["value"] == "300"
    assert m["denominators"]["average_allocated_assets"]["value"] is None
    assert m["denominators"]["average_allocated_assets"]["known_value"] == "1000"
    assert all(x["value"] is None for x in m["metrics"].values())
    d = decode(result["selection"], "spark", 2026)
    assert d["allocations"][8] == "a" * 64 and d["allocations"][0] is None
    assert d["expenses"] == source["revision_id"]
    assert result == project(s, a, source)
    assert s == {**statement(), "review_policy": {"enabled": True}}


def test_expense_leaf_sum_and_contributor_once():
    s = statement()
    source = snapshot("spark", 2026)
    cells = monthly(s, s["periods"][8], source)
    original = next(x for x in source["snapshot"]["lines"] if x["id"] == "expenses.total")[
        "values"
    ]["2026-09"]
    assert abs(Decimal(cells["expenses.total"]["value"]) - Decimal(original) - 17) < Decimal(".01")
    assert cells["expenses.total"]["quality"] == "estimate"
    s["periods"][8]["expected_cutoff"] = "2026-09-15"
    assert monthly(s, s["periods"][8], source)["expenses.total"]["value"] is None
    s["lines"][-1]["values"]["2026-09"] = metric(reason="missing")
    s["periods"][8]["expected_cutoff"] = "2026-09-30"
    assert monthly(s, s["periods"][8], source)["expenses.total"]["value"] is None


def test_ratios_zero_and_negative_income():
    assert ratio(metric("-10"), metric("100"))["value"] == "-0.1"
    assert ratio(metric("10"), metric("0"))["status"] == "not_applicable"
    assert ratio(metric("10"), metric())["value"] is None


def test_pinned_expenses_and_selection_validation():
    s = snapshot("spark", 2026)
    assert snapshot("spark", 2026, s["revision_id"]) == s
    with pytest.raises(ValueError):
        snapshot("spark", 2026, "0" * 64)
    with pytest.raises(ValueError):
        decode("garbage", "spark", 2026)


def test_http_endpoints_are_read_only_and_pin_allocations(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from settle.api import financial, revenue

    s = statement()
    for p in s["periods"]:
        p["financial_revision"] = None
    monkeypatch.setattr(financial, "statements", lambda *args, **kwargs: deepcopy(s))
    monkeypatch.setattr(revenue, "today", lambda: date(2026, 10, 1))
    calls = []

    class Reader:
        def financial_year(self, *args, **kwargs):
            return []

        def monthly_allocations(self, prime, month, revision=None):
            calls.append((prime, month, revision))
            return publication() if month == "2026-09" else None

    app = FastAPI()
    financial.register_financial(app, lambda: Reader(), lambda request, doc: doc)
    c = TestClient(app)
    result = c.get("/v3/reporting/margins?prime=spark&year=2026")
    assert result.status_code == 200, result.text
    assert len(calls) == 9
    assert result.json()["months"][8]["allocation_slice"]["metrics"]["nim"]["value"] == "0.016"
    assert c.get("/v3/reporting/margins?prime=grove&year=2026").status_code == 404
    assert c.get("/v3/reporting/margins?prime=spark&year=2026&revision=bad").status_code == 422
    assert c.get("/v3/reporting/margins?prime=spark&year=2026&cutoff=2026-10-05").status_code == 422
    exp = c.get("/v3/reporting/expenses?prime=spark&year=2026").json()
    assert exp["revision_id"] == snapshot("spark", 2026)["revision_id"]
    assert (
        c.get("/v3/reporting/expenses?prime=spark&year=2026&revision=" + "0" * 64).status_code
        == 404
    )
