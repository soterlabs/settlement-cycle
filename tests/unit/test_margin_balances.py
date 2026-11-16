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


def test_daily_scope_keeps_savings_idle_assets_and_savings_liabilities_separate():
    import gzip
    import json
    from pathlib import Path

    from settle.revenue.margin_balance_job import daily_row

    path = Path(__file__).parents[1] / "fixtures" / "spark_closing_stocks_october.json.gz"
    with gzip.open(path, "rt") as f:
        raw = json.load(f)
    from settle.revenue.verification import digest

    raw["extra_assets"]["alm_eth"] = {
        "status": "available",
        "value": "0",
        "quality": "supported",
        "read_indices": [],
    }
    raw["proof_hash"] = digest({k: v for k, v in raw.items() if k != "proof_hash"})
    extra = {
        "values": {
            "steakhouse": "3",
            "sky_staking": "0",
            **{
                k: "0"
                for k, v in raw["extra_assets"].items()
                if v.get("basis") == "cash_perimeter_exclusion"
            },
            "idle.S56": "7",
        }
    }
    row = daily_row(raw, extra)
    assert row["assets"]["idle.S56"] == "7"
    assert "S56" not in row["assets"]
    assert row["funding"]["S56"] == raw["liabilities"]["S56"]["value"]
    assert "xlayer_usdc" not in row["funding"]  # Its interest is not in the statement.
    assert row["assets"]["S62"] == raw["ownership"]["S62"]["prime_value"]
    assert "S23" not in row["assets"]  # Facility is added separately, once.
    del extra["values"]["idle.S56"]
    with pytest.raises(ValueError, match="supplement coverage"):
        daily_row(raw, extra)


def test_raw_evidence_cannot_be_omitted_or_replaced(monkeypatch):
    from settle.revenue import margin_balance_job
    from settle.revenue.margin_balances import validate_evidence

    doc = evidence()
    raw = {r["date"]: {"stocks": r, "supplement": {}} for r in doc["daily"]}
    monkeypatch.setattr(margin_balance_job, "daily_row", lambda stocks, _: stocks)
    validate_evidence(doc, raw)
    wrong = deepcopy(raw)
    wrong["2026-09-01"]["stocks"]["assets"]["venue"] = "0"
    with pytest.raises(ValueError, match="raw evidence"):
        validate_evidence(doc, wrong)
    raw.pop("2026-09-01")
    with pytest.raises(ValueError, match="coverage"):
        validate_evidence(doc, raw)


def test_new_savings_expense_cannot_use_an_old_narrower_funding_average():
    s = statement()
    s["lines"].append(
        {
            "id": "funding.savings",
            "components": [{"id": "funding.savings.venue.new_vault", "sign": 1}],
        }
    )
    balances = [None] * 12
    balances[8] = {"revision_id": "b" * 64, "document": evidence()}
    with pytest.raises(ValueError, match="funding perimeters"):
        project(s, [None] * 12, snapshot("spark", 2026), balances)


def test_average_publication_is_independent_of_python_hash_seed():
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = """
from settle.revenue.margin_balances import build, days
from settle.revenue.verification import digest
assets = {str(i): str(i + 1) + '.' + str(i % 9 + 1) * 95 for i in range(20)}
daily = [{"date": d, "assets": assets, "funding": {"sky_debt":"5"}, "evidence_hash":"a"*64} for d in days("2026-09")]
print(digest(build("2026-09", daily, "3", source_refs={"source":"b"*64}, universe={"assets":list(assets),"funding":["sky_debt"]})))
"""
    results = [
        subprocess.check_output(
            [sys.executable, "-c", script],
            env={
                **os.environ,
                "PYTHONHASHSEED": str(seed),
                "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
            },
            text=True,
        )
        for seed in (1, 2, 3)
    ]
    assert len(set(results)) == 1
