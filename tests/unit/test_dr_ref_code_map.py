"""Ref-code → prime attribution now lives in config/dr_ref_codes.yaml rather
than the retired Dune workbook's `group` column. These tests pin the
properties that protect settlement money: no code owned twice, no code in the
workbook left silently unowned, and the group total == the sum of its codes.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from settle.load import dr_rewards


REPO = Path(__file__).resolve().parents[2]
CFG = REPO / "config" / "dr_ref_codes.yaml"


def _cfg() -> dict:
    with CFG.open() as f:
        return yaml.safe_load(f)


def test_config_parses_and_has_no_duplicate_codes():
    owner, unattributed = dr_rewards._ref_code_map()
    assert owner, "expected at least one attributed ref code"
    assert not (set(owner) & set(unattributed)), (
        "a code cannot be both attributed and unattributed"
    )


def test_duplicate_code_across_primes_raises(tmp_path, monkeypatch):
    bad = tmp_path / "dr_ref_codes.yaml"
    bad.write_text(
        "primes:\n  spark:\n    - '128'\n  grove:\n    - '128'\n"
        "unattributed:\n  other: []\n"
    )
    monkeypatch.setattr(dr_rewards, "_REF_CODE_MAP_REL", bad.name)
    monkeypatch.setattr(dr_rewards, "_repo_root", lambda: tmp_path)
    dr_rewards._ref_code_map.cache_clear()
    try:
        with pytest.raises(ValueError, match="listed twice"):
            dr_rewards._ref_code_map()
    finally:
        dr_rewards._ref_code_map.cache_clear()


def test_duplicate_between_prime_and_unattributed_raises(tmp_path, monkeypatch):
    bad = tmp_path / "dr_ref_codes.yaml"
    bad.write_text(
        "primes:\n  spark:\n    - '1003'\n"
        "unattributed:\n  pending_methodology:\n    - '1003'\n"
    )
    monkeypatch.setattr(dr_rewards, "_REF_CODE_MAP_REL", bad.name)
    monkeypatch.setattr(dr_rewards, "_repo_root", lambda: tmp_path)
    dr_rewards._ref_code_map.cache_clear()
    try:
        with pytest.raises(ValueError, match="listed twice"):
            dr_rewards._ref_code_map()
    finally:
        dr_rewards._ref_code_map.cache_clear()


@pytest.mark.skipif(
    not (REPO / dr_rewards._WORKBOOK_REL).exists(),
    reason="settle-dr-dune submodule not initialised",
)
def test_every_workbook_code_is_accounted_for():
    """The property that matters: no ref code in the workbook is unowned.

    An unowned code is DR that reaches no prime. It must be either attributed
    or explicitly parked under `unattributed:`.
    """
    rows = dr_rewards._summary_rows()
    assert rows, "workbook present but no rows read"
    ref_col = dr_rewards._col_for(rows[0], "ref_code")
    assert ref_col is not None

    owner, unattributed = dr_rewards._ref_code_map()
    known = set(owner) | set(unattributed)

    missing = sorted(
        str(r[ref_col]).strip()
        for r in rows[1:]
        if r[ref_col] is not None
        and str(r[ref_col]).strip()
        and str(r[ref_col]).strip().lower() != "total"
        and str(r[ref_col]).strip() not in known
    )
    assert not missing, (
        f"ref codes in the DR workbook with no entry in {CFG.name}: {missing}. "
        "Attribute each to a prime or park it under `unattributed:`."
    )


@pytest.mark.skipif(
    not (REPO / dr_rewards._WORKBOOK_REL).exists(),
    reason="settle-dr-dune submodule not initialised",
)
def test_group_total_equals_sum_of_its_codes():
    """The HyperSync sheet has no Total row, so the total is derived. Guard
    the derivation rather than trusting it."""
    for prime in sorted(dr_rewards.dr_primes()):
        dr = dr_rewards.load_dr(prime, "2026-06")
        if dr is None:
            continue
        assert dr["total"] == sum(
            (r["amount"] for r in dr["rows"]), Decimal("0")
        ), f"{prime}: total does not equal the sum of its ref codes"


@pytest.mark.skipif(
    not (REPO / dr_rewards._WORKBOOK_REL).exists(),
    reason="settle-dr-dune submodule not initialised",
)
def test_prime_codes_are_disjoint_across_primes():
    seen: dict[str, str] = {}
    for prime in sorted(dr_rewards.dr_primes()):
        dr = dr_rewards.load_dr(prime, "2026-06")
        if dr is None:
            continue
        for row in dr["rows"]:
            code = row["ref_code"]
            assert code not in seen, (
                f"ref code {code} returned for both {seen[code]} and {prime}"
            )
            seen[code] = prime


def test_unknown_code_is_logged_at_error(monkeypatch, caplog):
    """A workbook code with no config entry must be loud, not silent."""
    header = ("ref_code", "2026-06", "notes")
    rows = (header, ("128", 100.0, ""), ("999999", 4242.42, "brand new"))
    monkeypatch.setattr(dr_rewards, "_summary_rows", lambda: rows)
    monkeypatch.setattr(
        dr_rewards, "_ref_code_map",
        lambda: ({"128": "spark"}, frozenset()),
    )
    with caplog.at_level(logging.ERROR):
        out = dr_rewards.load_dr("spark", "2026-06")
    assert out["total"] == Decimal("100.0")          # unknown code excluded
    assert "999999" in caplog.text
    assert "4,242.42" in caplog.text                  # amount named
    assert "EXCLUDED" in caplog.text


def test_obex_has_no_dr():
    assert "obex" not in dr_rewards.dr_primes()
    assert dr_rewards.load_dr("obex", "2026-06") is None
