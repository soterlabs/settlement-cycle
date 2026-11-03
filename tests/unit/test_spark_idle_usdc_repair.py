import copy
from decimal import Decimal as D

import pytest

from settle.revenue.spark_idle_usdc_repair import EVIDENCE, ROOT, VENUES, repair_document
from settle.revenue.verification import digest
from tests.unit.test_spark_s66_repair import base_document


def test_daily_cash_restores_idle_yields_without_changing_costs():
    base = base_document()
    original = copy.deepcopy(base)
    fixed = repair_document(base)
    expected = {
        "S45": D("2163.569507870967741935483871"),
        "S53": D("578.3861862580645161290322581"),
        "S55": D("1198.301396193548387096774194"),
    }
    assert base == original
    for old, new in zip(base["allocations"], fixed["allocations"], strict=True):
        if old["venue_id"] not in VENUES:
            assert old == new
            continue
        assert D(new["average_yield_exposure_usd"]) == expected[new["venue_id"]]
        assert D(new["gross_apy"]) == 0
        assert new["net_apy"] is new["borrowing_cost_usd"] is None
        estimate = new["conditional_funding_estimate"]
        assert D(estimate["net_apy"]) < 0
        assert estimate["funding_validated"] is False
        assert (
            estimate["borrowing_cost_usd"]
            == old["conditional_funding_estimate"]["borrowing_cost_usd"]
        )
        assert estimate["net_pnl_usd"] == old["conditional_funding_estimate"]["net_pnl_usd"]
    assert fixed["conditional_funding"] == base["conditional_funding"]
    assert repair_document(dict(fixed, revision_id=digest(fixed))) == fixed


@pytest.mark.parametrize(
    "change", ["period", "filtered", "cost", "income", "canonical", "validated"]
)
def test_rejects_different_scope_or_economics(change):
    base = base_document()
    row = next(r for r in base["allocations"] if r["venue_id"] == "S45")
    if change == "period":
        base["month"] = "2026-09"
    if change == "filtered":
        base["venue"] = "S45"
    if change == "cost":
        row["conditional_funding_estimate"]["borrowing_cost_usd"] = "0"
    if change == "income":
        row["gross_revenue_usd"] = "1"
    if change == "canonical":
        row["borrowing_cost_usd"] = "1"
    if change == "validated":
        row["conditional_funding_estimate"]["funding_validated"] = True
    with pytest.raises(ValueError):
        repair_document(base)


def test_refuses_changed_exposure(tmp_path):
    target = tmp_path / EVIDENCE
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT / EVIDENCE).read_bytes() + b" ")
    with pytest.raises(ValueError, match="evidence changed"):
        repair_document(base_document(), root=tmp_path)


def repair_publisher():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "repair_idle", ROOT / "scripts/repair_spark_idle_usdc_august.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_protects_all_pinned_inputs(tmp_path, monkeypatch):
    import json
    import sys

    output = tmp_path / "alias.json"
    output.symlink_to(ROOT / "reconciliation/spark_all_configured_funding_costs_2026_08.json.gz")
    base = tmp_path / "base.json"
    base.write_text(json.dumps(base_document()))
    monkeypatch.setattr(sys, "argv", ["repair", "--base", str(base), "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        repair_publisher().main()
    assert error.value.code == 2
