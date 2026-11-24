"""Address classification and reproducibility for the monitoring export."""

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "export_addresses", ROOT / "scripts/export_msc_addresses.py"
)
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def test_committed_export_is_current():
    assert (ROOT / "monitoring/msc_addresses.csv").read_text() == export.render(
        export.build_rows(ROOT / "config")
    )


def test_real_monitoring_boundaries_and_venue_context():
    rows = export.build_rows(ROOT / "config")
    assert {r["prime"] for r in rows} == {"spark", "grove", "obex", "osero", "keel", "skybase"}
    assert any(
        r["watch_alm"] == "true" and r["address"] == "0x0dcd9298e163dfd3c0b5b00f0d9093c36e40a153"
        for r in rows
    )
    assert all(r["watch_alm"] == "false" for r in rows if r["role"] == "subproxy")
    oracle = [
        r
        for r in rows
        if r["prime"] == "grove" and r["venue_id"] == "E20" and r["role"] == "oracle"
    ]
    assert oracle and all(r["chain"] == "ethereum" for r in oracle)
    assert any(
        r["venue_id"] == "E20" and r["role"] == "venue_token" and r["chain"] == "avalanche_c"
        for r in rows
    )
    assert any(
        r["venue_id"] == "S67" and r["msc_enabled"] == "false" and r["role"] == "payment_source"
        for r in rows
    )
    assert any(
        r["role"] == "external_yield_source" and r["start_date"] == "2026-09-01" for r in rows
    )
    assert any(r["role"] == "principal_return_override" for r in rows)
    assert any(
        r["source_file"] == "config/issuer_redemptions.yaml" and r["role"] == "redemption_payer"
        for r in rows
    )
    assert any(r["is_zero_address"] == "true" and r["watch_alm"] == "false" for r in rows)


def write_config(tmp_path, venue):
    doc = {
        "id": "test",
        "start_date": "2026-01-01",
        "addresses": {"ethereum": {"alm": "0x" + "aB" * 20}},
        "venues": [venue],
    }
    (tmp_path / "test.yaml").write_text(yaml.safe_dump(doc))


def test_legacy_disabled_flags_shared_addresses_and_embedded_hashes(tmp_path):
    address = "0x" + "aB" * 20
    venue = {
        "id": "V1",
        "label": "Example",
        "chain": "base",
        "display_only": True,
        "token": {"address": address, "symbol": "USDC"},
        "holder_override": address,
        "start_date": "2026-03-01",
        "end_date": "2026-09-30",
        "note": f"Ignore prose {address}",
        "transaction": "0x" + "ab" * 32,
        "new_route": address,
    }
    write_config(tmp_path, venue)
    rows = export.build_rows(tmp_path)
    assert len(rows) == 4  # preserve each role, ignore prose/transaction hashes
    assert {r["chain"] for r in rows} == {"ethereum", "base"}
    assert all(r["address"] == address.lower() for r in rows)
    assert all(
        r["msc_enabled"] == "false" and r["end_date"] == "2026-09-30" for r in rows if r["venue_id"]
    )
    assert any(r["role"] == "configured_address" for r in rows)  # unknown semantics stay visible


def test_ambiguous_chain_fails_instead_of_guessing_ethereum(tmp_path):
    write_config(tmp_path, {"id": "V1", "label": "Bad", "token": {"address": "0x" + "11" * 20}})
    with pytest.raises(ValueError, match="Unresolved chain"):
        export.build_rows(tmp_path)


def test_receipt_chain_overrides_venue_and_unknown_supplement_fails(tmp_path):
    write_config(
        tmp_path,
        {
            "id": "V1",
            "label": "Cross-chain",
            "chain": "base",
            "cash_distributions": [{"chain": "ethereum", "payer": "0x" + "22" * 20}],
        },
    )
    rows = export.build_rows(tmp_path)
    assert next(r for r in rows if r["role"] == "payment_source")["chain"] == "ethereum"
    (tmp_path / "redemptions.yaml").write_text(
        yaml.safe_dump({"venues": [{"prime": "test", "venue_id": "missing"}]})
    )
    with pytest.raises(ValueError, match="Unknown supplementary venue"):
        export.build_rows(tmp_path)


def test_payer_activation_uses_normalized_address(tmp_path):
    payer = "0x" + "AB" * 20
    doc = {
        "id": "test",
        "addresses": {},
        "start_date": "2026-01-01",
        "external_alm_sources": {"ethereum": [payer]},
        "external_alm_source_start_dates": {"ethereum": {payer.lower(): "2026-09-01"}},
    }
    (tmp_path / "test.yaml").write_text(yaml.safe_dump(doc))
    rows = export.build_rows(tmp_path)
    assert (
        next(r for r in rows if r["role"] == "external_yield_source")["start_date"] == "2026-09-01"
    )


@pytest.mark.parametrize("missing", [False, True])
def test_empty_or_missing_config_cannot_replace_inventory(tmp_path, missing):
    import subprocess
    import sys

    output = tmp_path / "inventory.csv"
    output.write_text("existing inventory\n")
    config = tmp_path / "missing" if missing else tmp_path
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/export_msc_addresses.py"),
            "--config-dir",
            str(config),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "No Prime configurations" in result.stderr
    assert output.read_text() == "existing inventory\n"


def test_supplement_inherits_parent_and_venue_date_bounds(tmp_path):
    write_config(
        tmp_path,
        {
            "id": "V1",
            "label": "Venue",
            "chain": "ethereum",
            "end_date": "2026-09-30",
            "token": {"address": "0x" + "11" * 20},
        },
    )
    (tmp_path / "redemptions.yaml").write_text(
        yaml.safe_dump(
            {
                "venues": [
                    {
                        "prime": "test",
                        "venue_id": "V1",
                        "cash_payer": "0x" + "22" * 20,
                        "start_date": "2025-01-01",
                        "end_date": "2026-12-31",
                    }
                ]
            }
        )
    )
    rows = export.build_rows(tmp_path)
    payer = next(r for r in rows if r["role"] == "redemption_payer")
    assert payer["start_date"] == "2026-01-01"
    assert payer["end_date"] == "2026-09-30"


@pytest.mark.parametrize("duplicate", ["Prime", "venue"])
def test_duplicate_ids_cannot_mislabel_supplementary_addresses(tmp_path, duplicate):
    venue = {
        "id": "V1",
        "label": "Original",
        "chain": "ethereum",
        "token": {"address": "0x" + "11" * 20},
    }
    write_config(tmp_path, venue)
    path = tmp_path / "test.yaml"
    doc = yaml.safe_load(path.read_text())
    if duplicate == "Prime":
        (tmp_path / "other.yaml").write_text(yaml.safe_dump(doc))
    else:
        doc["venues"].append({**venue, "chain": "base", "label": "Conflicting"})
        path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ValueError, match=f"Duplicate {duplicate} ID"):
        export.build_rows(tmp_path)
