"""Accrual-basis sky_total (2026-07 onward) — compute + failure modes."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from settle.compute.sky_total_accrual import compute_sky_total_accrual
from settle.domain import Month


def _write_prov(root, prime, label, sky, agent, dr):
    d = root / "settlements" / prime / label
    d.mkdir(parents=True)
    (d / "provenance.json").write_text(json.dumps({
        "results": {"sky_revenue": str(sky), "agent_rate": str(agent),
                    "distribution_rewards": str(dr)},
    }))


def _write_non_msc(root, label, income, expense):
    d = root / "settlements" / "non_msc" / label
    d.mkdir(parents=True)
    (d / "provenance.json").write_text(json.dumps({
        "results": {"total_income": str(income), "total_expense": str(expense)},
        "warnings": [],
    }))


def test_accrual_msc_net_and_snr(tmp_path):
    _write_prov(tmp_path, "spark", "2026-07", 100, 10, 5)
    _write_prov(tmp_path, "keel", "2026-07", 0, 3, 1)
    _write_non_msc(tmp_path, "2026-07", 50, 20)
    cfg = {"accrual_primes": ["spark", "keel"],
           "grove_tge_penalty": {"2026-07": 7}}
    r = compute_sky_total_accrual(Month(2026, 7), repo_root=tmp_path, config=cfg)
    assert r.msc_net == Decimal(100 - 10 - 5) + Decimal(0 - 3 - 1) - Decimal(7)
    assert r.non_msc_net == Decimal(30)
    assert r.sky_net_revenue == r.msc_net + Decimal(30)
    assert r.grove_tge_penalty_source == "config:2026-07"
    assert not r.warnings


def test_accrual_missing_prime_artifact_raises(tmp_path):
    _write_prov(tmp_path, "spark", "2026-07", 100, 10, 5)
    _write_non_msc(tmp_path, "2026-07", 0, 0)
    cfg = {"accrual_primes": ["spark", "osero"]}
    with pytest.raises(FileNotFoundError, match="osero"):
        compute_sky_total_accrual(Month(2026, 7), repo_root=tmp_path, config=cfg)


def test_accrual_unset_tge_penalty_warns(tmp_path):
    _write_prov(tmp_path, "spark", "2026-07", 100, 0, 0)
    _write_non_msc(tmp_path, "2026-07", 0, 0)
    cfg = {"accrual_primes": ["spark"]}
    r = compute_sky_total_accrual(Month(2026, 7), repo_root=tmp_path, config=cfg)
    assert r.grove_tge_penalty == 0 and r.grove_tge_penalty_source == "unset"
    assert any("grove_tge_penalty" in w for w in r.warnings)
