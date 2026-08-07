"""Unit tests for the accrual-basis sky_total (settlement-preview view)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from settle.compute.sky_total_accrual import (
    compute_sky_total_accrual,
    render_summary,
)
from settle.domain import Month


def _seed_prime(root: Path, prime: str, label: str, *, sky="0", par="0",
                ar="0", dr="0", cp="0", gar="0", sde="0") -> None:
    d = root / "settlements" / prime / label
    d.mkdir(parents=True)
    (d / "provenance.json").write_text(json.dumps({"results": {
        "sky_revenue": sky, "prime_agent_revenue": par, "agent_rate": ar,
        "distribution_rewards": dr, "chronicle_points": cp, "gar": gar,
        "sde_revenue": sde,
    }}))


def _seed_non_msc(root: Path, label: str, income: str, expense: str) -> None:
    d = root / "settlements" / "non_msc" / label
    d.mkdir(parents=True)
    (d / "provenance.json").write_text(json.dumps({
        "results": {"total_income": income, "total_expense": expense},
        "warnings": [],
    }))


def _july_repo(tmp_path: Path) -> Path:
    """The real July 2026 headline components (repo artifacts as of the
    2026-08-07 freeze)."""
    L = "2026-07"
    # par chosen so sv = par − (sky − sde) = 2,846,721.64
    _seed_prime(tmp_path, "spark", L, sky="5750694.26", par="8602620.66",
                ar="131173.46", dr="943409.92", sde="-5204.76")
    _seed_prime(tmp_path, "grove", L, sky="8003550.333979755082216437838",
                par="9567309.227536337846180999",  # sv = 1,563,758.89 (approx)
                ar="72023.79", dr="28898.24", cp="13102.0781773722182089824",
                sde="0.088")
    _seed_prime(tmp_path, "obex", L, sky="1761245.42", par="2525980.11",
                ar="71996.70", sde="0")
    _seed_prime(tmp_path, "osero", L, sky="496.99", par="390.30",
                ar="12094.56", dr="54.87", sde="0")
    _seed_prime(tmp_path, "keel", L, sky="0", par="0", ar="32003.53", dr="4227.11")
    # skybase provenance carries the POST-freeze gar (105,174.26); the
    # config gar_in_dv pin must swap the frozen 152,255.89 back in.
    _seed_prime(tmp_path, "skybase", L, sky="0", par="0", ar="37629.25",
                dr="95303.38", gar="105174.26")
    _seed_non_msc(tmp_path, L, "15638940.23", "19219232.4271")
    return tmp_path


_JULY_CFG = {
    "accrual_primes": ["spark", "grove", "obex", "osero", "keel", "skybase"],
    "allocator_ilks": {"spark": "x", "grove": "x", "obex": "x",
                       "grove_pau": "x", "osero": "x"},
    "msc_preview": {"2026-07": {
        "spark":   {"mint": 9465419, "send": 4442924, "dv_adj": -41908,
                    "sv_adj": 563527, "sky_adj": 304476},
        "grove":   {"mint": 9685438, "send": 1808084, "sv_adj": 52429,
                    "sky_adj": 65701, "send_credit": 77872},
        "obex":    {"mint": 2535968, "send": 916736, "dv_adj": -2601,
                    "sv_adj": 82606, "sky_adj": -72618},
        "osero":   {"mint": 497, "send": 12043},
        "keel":    {"mint": 0, "send": 35328, "dv_adj": -903},
        "skybase": {"mint": 0, "send": 374489, "dv_adj": 89300,
                    "gar_in_dv": 152255.89},
    }},
}


def test_july_2026_reproduces_frozen_sheet(tmp_path):
    """MSC net 14,097,718 and SNR 10,517,425.80 (the sheet's 10,517,426
    at whole-dollar display) from the pinned MSC#11 preview."""
    root = _july_repo(tmp_path)
    r = compute_sky_total_accrual(Month(2026, 7), repo_root=root, config=_JULY_CFG)
    assert r.total_mint == Decimal("21687322")
    assert r.total_send == Decimal("7589604")
    assert r.msc_net == Decimal("14097718")
    assert r.sky_net_revenue.quantize(Decimal("0.01")) == Decimal("10517425.80")
    # No cross-check warnings: every pinned figure within $2 of derived.
    assert not [w for w in r.warnings if "pinned" in w], r.warnings


def test_gar_in_dv_pin_makes_frozen_snr_reproducible(tmp_path):
    """The skybase report carries the post-freeze GAR (105,174.26); the
    config pin swaps the at-freeze value (152,255.89) back into the DV so
    re-runs keep reproducing the frozen SNR."""
    root = _july_repo(tmp_path)
    r = compute_sky_total_accrual(Month(2026, 7), repo_root=root, config=_JULY_CFG)
    skybase = next(row for row in r.rows if row.prime == "skybase")
    assert skybase.dv == Decimal("37629.25") + Decimal("95303.38") + Decimal("152255.89")
    assert abs(skybase.derived_send - Decimal("374488.52")) < Decimal("0.01")


def test_negative_supply_share_nets_in_send_not_mint(tmp_path):
    """Osero July: sv = −106.69 sits in the send; the mint is the Sky
    share alone."""
    root = _july_repo(tmp_path)
    r = compute_sky_total_accrual(Month(2026, 7), repo_root=root, config=_JULY_CFG)
    osero = next(row for row in r.rows if row.prime == "osero")
    assert osero.mint == Decimal("497")
    assert osero.derived_mint == Decimal("496.99")
    assert abs(osero.derived_send - Decimal("12042.74")) < Decimal("0.01")


def test_pin_drift_beyond_tolerance_warns(tmp_path):
    root = _july_repo(tmp_path)
    cfg = json.loads(json.dumps(_JULY_CFG))
    cfg["msc_preview"]["2026-07"]["obex"]["mint"] = 2600000  # 64K off
    r = compute_sky_total_accrual(Month(2026, 7), repo_root=root, config=cfg)
    assert any("obex mint: pinned" in w for w in r.warnings)


def test_unpinned_prime_uses_rounded_derivation(tmp_path):
    root = _july_repo(tmp_path)
    cfg = json.loads(json.dumps(_JULY_CFG))
    del cfg["msc_preview"]["2026-07"]["osero"]["mint"]
    del cfg["msc_preview"]["2026-07"]["osero"]["send"]
    r = compute_sky_total_accrual(Month(2026, 7), repo_root=root, config=cfg)
    osero = next(row for row in r.rows if row.prime == "osero")
    assert osero.mint == Decimal("497")
    assert osero.send == Decimal("12043")


def test_missing_prime_artifact_fails_loud(tmp_path):
    root = _july_repo(tmp_path)
    import shutil
    shutil.rmtree(root / "settlements" / "keel")
    with pytest.raises(FileNotFoundError, match="keel"):
        compute_sky_total_accrual(Month(2026, 7), repo_root=root, config=_JULY_CFG)


def test_render_summary_carries_headline(tmp_path):
    root = _july_repo(tmp_path)
    r = compute_sky_total_accrual(Month(2026, 7), repo_root=root, config=_JULY_CFG)
    md = render_summary(r)
    assert "10,517,425.80" in md
    assert "14,097,718" in md
    assert "ACCRUAL basis" in md
