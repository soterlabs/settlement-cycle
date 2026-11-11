"""The retained October close changes only reviewed Grove venue income."""

import copy
import importlib.util
import json
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml

from settle.compute import sky_total_accrual
from settle.domain import Month
from settle.load import writer
from settle.load.provenance import render_provenance
from settle.revenue.verification import canonical


@pytest.fixture
def replay_module():
    root = Path(__file__).parents[2]
    spec = importlib.util.spec_from_file_location(
        "grove_pr280_replay", root / "scripts/replay_october_grove_pr280.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_decode_retains_financial_snapshot_and_rejects_missing_cash_field(replay_module):
    prov = json.loads((replay_module.FOLDER / "before-provenance.json").read_text())
    decoded = render_provenance(replay_module.decode_provenance(prov))
    for key in ["results", "venue_breakdown", "reporting_only_breakdown", "sky_revenue_daily"]:
        assert decoded[key] == prov[key]
    del prov["venue_breakdown"][0]["value_som"]
    with pytest.raises(ValueError, match="Missing financial venue field value_som"):
        replay_module.decode_provenance(prov)


def test_offline_october_cash_replay_and_production_msc_identity(
    replay_module, tmp_path, monkeypatch
):
    # Tests isolate the local review gate; the actual recorded replay ran it unchanged.
    monkeypatch.setattr(
        replay_module, "run_path", lambda _: {"check_review": lambda p: "test-only"}
    )
    # This synthetic rendering test isolates the separately tested asset gate.
    monkeypatch.setattr(writer, "require_asset_controls", lambda *a, **kw: {"test_only": True})
    output = tmp_path / "close"
    result = replay_module.replay(output)
    assert result["income_delta"] == "8064.955289965368347474800"
    before = json.loads((replay_module.FOLDER / "before-provenance.json").read_text())
    after = json.loads((output / "provenance.json").read_text())
    for key in ["sky_revenue_daily", "sde_daily_breakdown", "reporting_only_breakdown"]:
        assert after[key] == before[key]
    old = {r["venue_id"]: r for r in before["venue_breakdown"]}
    for row in after["venue_breakdown"]:
        if row["venue_id"] not in result["replayed_venues"]:
            assert row == old[row["venue_id"]]
        else:
            assert row["tw_avg_value_usd"] == old[row["venue_id"]]["tw_avg_value_usd"]
    for key in [
        "agent_rate",
        "distribution_rewards",
        "chronicle_points",
        "sky_revenue",
        "sde_revenue",
    ]:
        assert after["results"][key] == before["results"][key]

    # Exercise the full production formula in a unit test with non-MSC isolated.
    # The real replay deliberately never calculates unrelated non-MSC/other primes.
    folder = tmp_path / "settlements/grove/2026-10"
    folder.mkdir(parents=True)
    (folder / "provenance.json").write_text(json.dumps(after))
    cfg = yaml.safe_load((replay_module.ROOT / "config/sky_total.yaml").read_text())
    cfg = copy.deepcopy(cfg)
    cfg["accrual_primes"] = ["grove"]
    cfg["msc_preview"] = {"2026-10": {"grove": cfg["msc_preview"]["2026-10"]["grove"]}}
    monkeypatch.setattr(sky_total_accrual, "_load_non_msc", lambda *a: (D(0), D(0), []))
    full = sky_total_accrual.compute_sky_total_accrual(
        Month(2026, 10), repo_root=tmp_path, config=cfg
    )
    assert canonical(full.rows[0]) == result["msc_instruction"]
    assert result["msc_instruction"]["mint"] == "8866792"
    assert result["msc_instruction"]["send"] == "2275969"


def test_missing_transfer_review_stops_before_calculation(replay_module, tmp_path, monkeypatch):
    def reject(prime):
        raise ValueError("Missing reviewed inventory")

    monkeypatch.setattr(replay_module, "run_path", lambda _: {"check_review": reject})
    with pytest.raises(ValueError, match="Missing reviewed inventory"):
        replay_module.replay(tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_new_asset_controls_gate_stops_canonical_replay(replay_module, tmp_path, monkeypatch):
    from settle.controls.assets import AssetControlError

    monkeypatch.setattr(replay_module, "run_path", lambda _: {"check_review": lambda p: "test-only"})
    monkeypatch.setattr(writer, "_REPO_ROOT", tmp_path)
    output = tmp_path / "uncertified"
    with pytest.raises(AssetControlError, match="Missing independent asset controls"):
        replay_module.replay(output)
    assert not (output / "provenance.json").exists()
    assert not (output / "summary.md").exists()
