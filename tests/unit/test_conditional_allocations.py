"""Conditional estimates never replace unresolved canonical values."""

import copy
import importlib.util
import json
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from settle.api.app import create_app
from settle.revenue.conditional_allocations import build_conditional_document
from tests.unit.test_monthly_allocation_api import Reader, documents

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "reconciliation/spark_conditional_publication_2026_08.json"


def publisher():
    spec = importlib.util.spec_from_file_location(
        "conditional_publisher", ROOT / "scripts/publish_conditional_allocations.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def baseline():
    return {**next(d for d in documents() if d["prime"] == "spark"), "revision_id": "a" * 64}


def evidence():
    import gzip

    manifest = json.loads(MANIFEST.read_text())
    result = {}
    for name, entry in manifest["evidence"].items():
        p = ROOT / entry["path"]
        result[name] = json.loads(
            gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
        )
    return result


def test_full_real_artifact_keeps_canonical_fields_and_unavailable_yields():
    base = baseline()
    original = copy.deepcopy(base)
    result = publisher().prepare(base, MANIFEST)
    assert base == original
    assert result["count"] == base["count"]
    assert result["conditional_funding"]["allocation_count"] == 56
    assert result["conditional_funding"]["funding_reconciliation_status"] == "unresolved"
    for old, row in zip(base["allocations"], result["allocations"], strict=True):
        assert {k: v for k, v in row.items() if k != "conditional_funding_estimate"} == old
    rows = {r["venue_id"]: r for r in result["allocations"]}
    assert rows["S1"]["borrowing_cost_usd"] is None and rows["S1"]["net_apy"] is None
    assert D(rows["S1"]["conditional_funding_estimate"]["borrowing_cost_usd"]) > 0
    assert D(rows["S66"]["conditional_funding_estimate"]["borrowing_cost_usd"]) == D(
        "2545.238240225381282622421558"
    )
    assert rows["S66"]["conditional_funding_estimate"]["net_pnl_usd"] is None
    assert rows["S66"]["conditional_funding_estimate"]["net_apy"] is None
    assert rows["S56"].get("conditional_funding_estimate") is None


def test_http_filters_preserve_qualified_estimate_and_canonical_nulls():
    reader = Reader()
    reader.documents["spark"] = {
        **publisher().prepare(baseline(), MANIFEST),
        "revision_id": "c" * 64,
    }
    response = TestClient(create_app(reader)).get(
        "/v1/revenue/spark/monthly/2026-08/allocations?venue=S66"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["allocations"][0]["borrowing_cost_usd"] is None
    assert (
        data["allocations"][0]["conditional_funding_estimate"]["status"] == "conditional_estimate"
    )


@pytest.mark.parametrize(
    "change", ["cost", "net_apy", "principal", "period", "coverage", "revenue"]
)
def test_changed_numeric_inputs_are_rejected(change):
    inputs = evidence()
    base = baseline()
    row = inputs["estimates"]["allocations"][0]
    if change == "cost":
        row["conditional_sky_borrowing_cost"] = "999"
    if change == "net_apy":
        row["conditional_net_apy"] = ".99"
    if change == "principal":
        row["average_sky_principal"] = "1"
    if change == "period":
        inputs["estimates"]["period"] = "2026-09"
    if change == "coverage":
        inputs["estimates"]["allocations"].pop()
    if change == "revenue":
        next(r for r in base["allocations"] if r["venue_id"] == row["venue"])[
            "prime_retained_revenue_usd"
        ] = "999"
    with pytest.raises(ValueError):
        build_conditional_document(
            base,
            inputs["estimates"],
            inputs["bridge"],
            inputs["savings"],
            inputs["control"],
            inputs["anchorage"],
            source_hashes={"test": "a" * 64},
        )


def test_unreviewed_file_hash_and_wrong_prime_are_rejected(tmp_path):
    manifest = json.loads(MANIFEST.read_text())
    manifest["evidence"]["estimates"]["sha256"] = "0" * 64
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="hash differs"):
        publisher().prepare(baseline(), path)
    base = baseline()
    base["prime"] = "grove"
    with pytest.raises(ValueError, match="scope"):
        publisher().prepare(base, MANIFEST)


def test_dry_run_cannot_overwrite_manifest_evidence(tmp_path, monkeypatch):
    import sys

    module = publisher()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    source = tmp_path / "evidence.json"
    source.write_text('{"preserve": true}')
    base = tmp_path / "base.json"
    base.write_text("{}")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"evidence": {"estimates": {"path": source.name}}}))
    alias = tmp_path / "output.json"
    alias.symlink_to(source)
    monkeypatch.setattr(
        sys,
        "argv",
        ["publisher", "--base", str(base), "--manifest", str(manifest), "--output", str(alias)],
    )
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert source.read_text() == '{"preserve": true}'
