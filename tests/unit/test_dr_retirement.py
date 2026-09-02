"""Per-prime DR retirement — ``config/dr_ref_codes.yaml → retired_from``.

Keel stops earning Distribution Rewards from 2026-08 (operator decision
2026-09). Expressed as a date bound rather than by removing Keel's ref
codes, for two reasons:

* 2026-01…07 are settled and must stay reproducible. Their
  ``provenance.json`` is gitignored, so a value lost to a re-run is gone.
* Keel's codes (4001, 4011) still appear in the DR workbook. Leaving them
  under ``primes:`` keeps the unknown-code guard covering them — that
  guard is what stops a newly-appearing code from silently going unpaid.

The gate returns an explicit ZERO, not ``None``. ``None`` means "no data,
leave the report alone", which would strand the last non-zero DR on an
already-written provenance when ``refresh_dr_only`` sweeps it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from settle.load import dr_rewards

REPO = Path(__file__).resolve().parents[2]


# --- the bound ------------------------------------------------------------

def test_keel_is_retired_from_2026_08():
    assert dr_rewards._dr_retired_from() == {"keel": "2026-08"}


def test_keel_codes_remain_attributed():
    """Retiring the payout must NOT unattribute the codes, or the workbook's
    4001/4011 rows would start tripping the unknown-code warning."""
    owner, _ = dr_rewards._ref_code_map()
    assert owner.get("4001") == "keel"
    assert owner.get("4011") == "keel"


@pytest.mark.parametrize("month,zero", [
    ("2026-06", False),
    ("2026-07", False),   # last month Keel earns DR
    ("2026-08", True),    # RETIRED from here
    ("2026-09", True),
    ("2027-01", True),
])
def test_keel_dr_by_month(month, zero):
    dr = dr_rewards.load_dr("keel", month)
    assert dr is not None, "keel is still a known DR prime"
    if zero:
        assert dr["total"] == Decimal("0")
        assert dr["rows"] == [], "a zero month must carry no ref-code rows"
    else:
        assert dr["total"] > 0


def test_retired_month_returns_zero_not_none():
    """The distinction that matters for ``refresh_dr_only``: None leaves the
    previously-written DR in place, an explicit zero overwrites it."""
    dr = dr_rewards.load_dr("keel", "2026-08")
    assert dr is not None
    assert dr["total"] == Decimal("0")


def test_other_primes_are_untouched():
    for prime in ("spark", "grove", "skybase", "osero"):
        dr = dr_rewards.load_dr(prime, "2026-08")
        if dr is None:
            continue                       # no DR group for this prime
        assert dr["total"] > 0, f"{prime} lost its August DR"


# --- config hygiene -------------------------------------------------------

def _with_config(tmp_path, monkeypatch, text: str):
    bad = tmp_path / "dr_ref_codes.yaml"
    bad.write_text(text)
    monkeypatch.setattr(dr_rewards, "_REF_CODE_MAP_REL", bad.name)
    monkeypatch.setattr(dr_rewards, "_repo_root", lambda: tmp_path)
    dr_rewards._ref_code_map.cache_clear()


def test_month_is_normalised(tmp_path, monkeypatch):
    """An unpadded month must not silently break the lexicographic compare:
    '2026-8' > '2026-12' as raw strings."""
    _with_config(tmp_path, monkeypatch,
                 "retired_from:\n  keel: '2026-8'\nprimes:\n  keel:\n    - '4001'\n")
    try:
        assert dr_rewards._dr_retired_from() == {"keel": "2026-08"}
    finally:
        dr_rewards._ref_code_map.cache_clear()


def test_malformed_month_is_rejected(tmp_path, monkeypatch):
    _with_config(tmp_path, monkeypatch,
                 "retired_from:\n  keel: 'whenever'\nprimes:\n  keel:\n    - '4001'\n")
    try:
        with pytest.raises(ValueError, match="is not a 'YYYY-MM' month"):
            dr_rewards._dr_retired_from()
    finally:
        dr_rewards._ref_code_map.cache_clear()


def test_absent_section_means_nobody_is_retired(tmp_path, monkeypatch):
    _with_config(tmp_path, monkeypatch, "primes:\n  keel:\n    - '4001'\n")
    try:
        assert dr_rewards._dr_retired_from() == {}
    finally:
        dr_rewards._ref_code_map.cache_clear()
