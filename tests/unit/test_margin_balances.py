from copy import deepcopy
from decimal import Decimal

import pytest

from settle.revenue.margin_balances import build, days, validate
from settle.revenue.margins import decode, project
from settle.revenue.reporting_expenses import snapshot
from tests.unit.test_reporting_margins import statement


def evidence():
    daily = [
        {
            "date": day,
            "assets": {"venue": str(100 + i)},
            "funding": {"sky_debt": "80", "savings": "10"},
            "evidence_hash": "a" * 64,
        }
        for i, day in enumerate(days("2026-09"))
    ]
    return build(
        "2026-09",
        daily,
        "5",
        source_refs={"source": "a" * 64},
        universe={"assets": ["venue"], "funding": ["sky_debt", "savings"]},
    )


def test_actual_daily_average_and_facility_once():
    doc = evidence()
    assert Decimal(doc["average_allocated_assets"]) == Decimal("119.5")
    assert Decimal(doc["average_funding"]) == Decimal("90")
    assert validate(doc) == doc
    bad = deepcopy(doc)
    bad["average_allocated_assets"] = "120"
    with pytest.raises(ValueError, match="arithmetic"):
        validate(bad)


@pytest.mark.parametrize(
    "change", ["missing_day", "duplicate_day", "missing_component", "nonfinite", "negative"]
)
def test_incomplete_or_invalid_balances_never_publish(change):
    doc = evidence()
    if change == "missing_day":
        doc["daily"].pop()
    if change == "duplicate_day":
        doc["daily"][1]["date"] = doc["daily"][0]["date"]
    if change == "missing_component":
        del doc["daily"][0]["funding"]["savings"]
    if change in {"nonfinite", "negative"}:
        doc["daily"][0]["assets"]["venue"] = "NaN" if change == "nonfinite" else "-1"
    with pytest.raises(ValueError):
        validate(doc)


def test_margins_use_statement_numerators_and_actual_liabilities():
    s = statement()
    s["review_policy"] = {"enabled": True}
    balances = [None] * 12
    balances[8] = {"revision_id": "b" * 64, "document": evidence()}
    result = project(s, [None] * 12, snapshot("spark", 2026), balances)
    month = result["months"][8]
    assert all(c["status"] == "available" for c in month["metrics"].values())
    assert Decimal(month["metrics"]["funding_rate"]["value"]) == Decimal(300) / 90 or abs(
        Decimal(month["metrics"]["funding_rate"]["value"]) - Decimal(300) / 90
    ) < Decimal("1e-25")
    assert decode(result["selection"], "spark", 2026)["balances"][8] == "b" * 64
    assert month["allocation_slice"]["status"] == "unavailable"
    assert result["months"][0]["metrics"]["nim"]["value"] is None
    assert s == {**statement(), "review_policy": {"enabled": True}}


def test_old_pinned_selection_remains_without_new_balances():
    s = statement()
    s["review_policy"] = {"enabled": True}
    result = project(s, [None] * 12, snapshot("spark", 2026))
    assert "balances" not in decode(result["selection"], "spark", 2026)
    assert all(m["metrics"]["nim"]["value"] is None for m in result["months"])
