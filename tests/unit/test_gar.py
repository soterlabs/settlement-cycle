"""Unit tests for compute/gar.py — the GAR primitive's basis rules."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from settle.compute.gar import compute_gar
from settle.domain.period import Month
from settle.domain.primes import GarConfig


def _prime(gar: GarConfig | None) -> SimpleNamespace:
    # compute_gar only touches .gar and .id — a stub keeps the test free of
    # the full Prime constructor.
    return SimpleNamespace(id="skybase", gar=gar)


def _seed_snr(root: Path, label: str, snr: str) -> None:
    d = root / "settlements" / "sky_total" / label
    d.mkdir(parents=True)
    (d / "provenance.json").write_text(json.dumps({
        "id": "sky_total",
        "generated_at_utc": "2026-08-06T00:00:00+00:00",
        "results": {"sky_net_revenue": snr},
    }))


_CFG = GarConfig(share=Decimal("0.01"), from_month="2026-01")


def test_no_gar_config_is_silent(tmp_path):
    gar, basis = compute_gar(_prime(None), Month(2026, 7), repo_root=tmp_path)
    assert (gar, basis) == (Decimal("0"), "")


def test_month_before_from_month_is_silent(tmp_path):
    cfg = GarConfig(share=Decimal("0.01"), from_month="2026-07")
    gar, basis = compute_gar(_prime(cfg), Month(2026, 6), repo_root=tmp_path)
    assert (gar, basis) == (Decimal("0"), "")


def test_base_month_before_series_renders_na(tmp_path):
    """The 2026-01 report's base is 2025-12 — before the sky_total series:
    $0 booked, basis carries the n/a marker for the summary row."""
    gar, basis = compute_gar(_prime(_CFG), Month(2026, 1), repo_root=tmp_path)
    assert gar == Decimal("0")
    assert basis.startswith("n/a")
    assert "2025-12" in basis


def test_gar_is_share_times_prior_month_snr(tmp_path):
    _seed_snr(tmp_path, "2026-06", "14875074.63")
    gar, basis = compute_gar(_prime(_CFG), Month(2026, 7), repo_root=tmp_path)
    assert gar == Decimal("0.01") * Decimal("14875074.63")
    assert "2026-06" in basis and "14875074.63" in basis


def test_missing_base_artifact_inside_series_fails_loud(tmp_path):
    with pytest.raises(FileNotFoundError, match="2026-05"):
        compute_gar(_prime(_CFG), Month(2026, 6), repo_root=tmp_path)


def test_negative_base_snr_floors_to_zero(tmp_path):
    _seed_snr(tmp_path, "2026-02", "-972786.88")
    gar, basis = compute_gar(_prime(_CFG), Month(2026, 3), repo_root=tmp_path)
    assert gar == Decimal("0")
    assert "NEGATIVE" in basis and "floored" in basis
