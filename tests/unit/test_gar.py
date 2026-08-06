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


def test_gar_is_share_times_same_month_snr(tmp_path):
    """The month-N report carries 1% × SNR(N); the cash is paid at the MSC
    executing in N+1 (July's GAR rides MSC#11 in August)."""
    _seed_snr(tmp_path, "2026-07", "15225588.81")
    gar, basis = compute_gar(_prime(_CFG), Month(2026, 7), repo_root=tmp_path)
    assert gar == Decimal("0.01") * Decimal("15225588.81")
    assert "2026-07" in basis and "15225588.81" in basis


def test_missing_artifact_fails_loud(tmp_path):
    with pytest.raises(FileNotFoundError, match="2026-06"):
        compute_gar(_prime(_CFG), Month(2026, 6), repo_root=tmp_path)


def test_negative_snr_floors_to_zero(tmp_path):
    _seed_snr(tmp_path, "2026-03", "-972786.88")
    gar, basis = compute_gar(_prime(_CFG), Month(2026, 3), repo_root=tmp_path)
    assert gar == Decimal("0")
    assert "NEGATIVE" in basis and "floored" in basis
