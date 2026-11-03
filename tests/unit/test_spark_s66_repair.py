"""Finalized S66 revenue, full exposure and unchanged conditional financing."""

import copy
import importlib.util
import json
from decimal import Decimal as D

import pytest

from settle.revenue.spark_s66_repair import MANIFEST, ROOT, calculate, repair_document
from settle.revenue.verification import digest
from tests.unit.test_conditional_allocations import MANIFEST as CONDITIONAL_MANIFEST
from tests.unit.test_conditional_allocations import baseline, publisher


def base_document():
    doc = publisher().prepare(baseline(), CONDITIONAL_MANIFEST)
    return dict(doc, revision_id=digest(doc))


def repair_publisher():
    spec = importlib.util.spec_from_file_location(
        "repair_s66", ROOT / "scripts/repair_spark_s66_august.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_finalized_revenue_full_exposure_and_daily_cost_agree():
    result = calculate()
    assert D(result["gross_revenue_usd"]) == D("-461.370572920806")
    assert D(result["borrowing_cost_usd"]) == D("2545.238240225381282622421558")
    assert D(result["workbook_cost_allocation_usd"]) == D("3186.901270819541")
    assert D(result["average_yield_exposure_usd"]) == D("3798547.027551647915645714548")
    assert D(result["gross_apy"]) == D("-0.0014291552465153291828359312762787658539")
    assert D(result["net_apy"]) == D("-0.0092798209903286594790725539081422150922")
    assert len(result["daily"]) == 31
    assert sum(D(r["borrowing_cost_usd"]) for r in result["daily"].values()) == D(
        result["borrowing_cost_usd"]
    )
    assert all(
        D(r["borrowing_cost_usd"]) == 0 for day, r in result["daily"].items() if day < "2026-08-27"
    )
    for row in result["daily"].values():
        assert D(row["chargeable_sky_principal_usd"]) == max(
            D(0), D(row["sky_principal_usd"]) - D(row["idle_usds"])
        )


def test_only_s66_changes_without_promoting_conditional_funding():
    base = base_document()
    original = copy.deepcopy(base)
    repaired = repair_document(base)
    assert base == original
    for old, new in zip(base["allocations"], repaired["allocations"], strict=True):
        if old["venue_id"] != "S66":
            assert old == new
        else:
            assert new["gross_apy"] is not None
            assert new["net_apy"] is new["borrowing_cost_usd"] is None
            assert new["conditional_funding_estimate"]["net_apy"] is not None
            assert new["conditional_funding_estimate"]["funding_validated"] is False
            assert (
                new["conditional_funding_estimate"]["borrowing_cost_usd"]
                == old["conditional_funding_estimate"]["borrowing_cost_usd"]
            )
    assert repaired["conditional_funding"] == base["conditional_funding"]
    assert repaired["count"] == base["count"]
    assert repair_document(dict(repaired, revision_id=digest(repaired))) == repaired


@pytest.mark.parametrize(
    "change", ["prime", "month", "filter", "cost", "revenue", "canonical", "funding"]
)
def test_rejects_scope_or_baseline_disagreement(change):
    base = base_document()
    row = next(r for r in base["allocations"] if r["venue_id"] == "S66")
    if change == "prime":
        base["prime"] = "grove"
    if change == "month":
        base["month"] = "2026-09"
    if change == "filter":
        base["venue"] = "S66"
    if change == "cost":
        row["conditional_funding_estimate"]["borrowing_cost_usd"] = "0"
    if change == "revenue":
        row["gross_revenue_usd"] = "999"
    if change == "funding":
        row["conditional_funding_estimate"]["average_sky_principal_usd"] = "0"
    if change == "canonical":
        row["borrowing_cost_usd"] = "123"
    with pytest.raises(ValueError):
        repair_document(base)


def test_changed_pinned_evidence_is_rejected(tmp_path):
    manifest = json.loads((ROOT / MANIFEST).read_text())
    for entry in manifest["evidence"].values():
        source = ROOT / entry["path"]
        dest = tmp_path / entry["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
    target = tmp_path / MANIFEST
    target.write_text(json.dumps(manifest))
    (tmp_path / manifest["evidence"]["idle"]["path"]).write_text("{}")
    with pytest.raises(ValueError, match="evidence changed"):
        calculate(tmp_path)


def test_cli_refuses_to_overwrite_evidence_even_via_symlink(tmp_path, monkeypatch):
    import sys

    output = tmp_path / "alias.json"
    output.symlink_to(ROOT / "reconciliation/spark_s66_august_idle_usds.json")
    base = tmp_path / "base.json"
    base.write_text(json.dumps(base_document()))
    monkeypatch.setattr(sys, "argv", ["repair", "--base", str(base), "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        repair_publisher().main()
    assert error.value.code == 2
