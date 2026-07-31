"""Unit tests for ``settle.domain.subsidy`` — reference-rate carry-forward,
stale-data guards, and the subsidised-rate ramp.

These paths were untested through 2026-05. The new fatal-threshold guard
(``_STALE_CARRY_FORWARD_FATAL_DAYS``) added in Round 1 of the codebase
review materially changes the failure mode for runs against stale config
— it turns silently wrong sky_revenue into a loud crash. Locking the
behavior with tests prevents regressions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from settle.domain.subsidy import (
    ReferenceRateHistory,
    months_elapsed_since,
    subsidised_apy,
)


def _hist(rows: list[tuple[str, float]], kind: str = "tbill_3m") -> ReferenceRateHistory:
    return ReferenceRateHistory(
        rates=pd.DataFrame({
            "effective_date": [date.fromisoformat(d) for d, _ in rows],
            "ref_rate_apy":   [r for _, r in rows],
        }),
        kind=kind,
    )


# --- ReferenceRateHistory.at ------------------------------------------------

def test_at_returns_latest_le_target():
    h = _hist([("2026-01-15", 0.040), ("2026-02-01", 0.042), ("2026-03-30", 0.0358)])
    assert h.at(date(2026, 1, 16)) == Decimal("0.040")
    assert h.at(date(2026, 2, 1))  == Decimal("0.042")
    assert h.at(date(2026, 3, 15)) == Decimal("0.042")
    assert h.at(date(2026, 3, 30)) == Decimal("0.0358")


def test_at_raises_when_no_rate_before_target():
    h = _hist([("2026-02-01", 0.04)])
    with pytest.raises(ValueError, match="No reference rate found"):
        h.at(date(2026, 1, 15))


def test_at_warns_when_stale_within_threshold(caplog):
    """22 days of carry-forward → WARNING but still returns the value."""
    h = _hist([("2026-03-30", 0.0358)])
    caplog.clear()
    with caplog.at_level("WARNING"):
        val = h.at(date(2026, 4, 21))  # 22 days stale
    assert val == Decimal("0.0358")
    assert any("carried forward" in rec.message for rec in caplog.records)


def test_at_raises_when_stale_beyond_fatal_threshold():
    """46+ days stale → ValueError (post-Round-1 fix); silently using a
    month-old rate would materially mis-price the subsidy."""
    h = _hist([("2026-03-30", 0.0358)])
    # 46 days stale = 2026-05-15
    with pytest.raises(ValueError, match="fatal threshold"):
        h.at(date(2026, 5, 15))


def test_at_fresh_no_warning(caplog):
    """In-the-same-week rate → no warning."""
    h = _hist([("2026-03-30", 0.0358)])
    caplog.clear()
    with caplog.at_level("WARNING"):
        val = h.at(date(2026, 4, 2))  # 3 days stale
    assert val == Decimal("0.0358")
    assert not any("carried forward" in rec.message for rec in caplog.records)


# --- subsidised_apy ---------------------------------------------------------

def test_subsidised_apy_t_zero_full_subsidy():
    """T=0: subsidised = ref_rate (full subsidy)."""
    out = subsidised_apy(
        base_apy=Decimal("0.043"),
        ref_rate_apy=Decimal("0.0367"),
        months_elapsed=0,
    )
    assert out == Decimal("0.0367")


def test_subsidised_apy_t_full_ramp_returns_base():
    """T=24 (or above): subsidised = base_apy (no subsidy)."""
    out = subsidised_apy(
        base_apy=Decimal("0.043"),
        ref_rate_apy=Decimal("0.0367"),
        months_elapsed=24,
    )
    assert out == Decimal("0.043")


def test_subsidised_apy_midway():
    """T=12: halfway between ref_rate and base_apy."""
    out = subsidised_apy(
        base_apy=Decimal("0.043"),
        ref_rate_apy=Decimal("0.0367"),
        months_elapsed=12,
    )
    expected = Decimal("0.0367") + (Decimal("0.043") - Decimal("0.0367")) * Decimal("12") / Decimal("24")
    assert out == expected


def test_subsidised_apy_clamps_when_ref_rate_above_base():
    """ref_rate (4.33%) > BR (4.30%): clamp at base_apy, prime never pays MORE
    than the unsubsidised rate. This is the live Spark Jan-Feb 2026 case."""
    out = subsidised_apy(
        base_apy=Decimal("0.043"),
        ref_rate_apy=Decimal("0.0433"),
        months_elapsed=0,
    )
    # Without the clamp, T=0 would give 4.33% (above 4.30%).
    assert out == Decimal("0.043")


def test_subsidised_apy_negative_months_treated_as_zero():
    """Period entirely before program_start (negative T) → full subsidy."""
    out = subsidised_apy(
        base_apy=Decimal("0.043"),
        ref_rate_apy=Decimal("0.0367"),
        months_elapsed=-3,
    )
    assert out == Decimal("0.0367")


# --- months_elapsed_since ---------------------------------------------------

def test_months_elapsed_jan_is_zero():
    """Per the docstring: Jan 2026 → 0, Feb 2026 → 1."""
    assert months_elapsed_since(date(2026, 1, 1),  date(2026, 1, 1)) == 0
    assert months_elapsed_since(date(2026, 1, 31), date(2026, 1, 1)) == 0
    assert months_elapsed_since(date(2026, 2, 1),  date(2026, 1, 1)) == 1
    assert months_elapsed_since(date(2026, 3, 1),  date(2026, 1, 1)) == 2
    assert months_elapsed_since(date(2027, 1, 1),  date(2026, 1, 1)) == 12


def test_months_elapsed_before_anchor_returns_zero():
    assert months_elapsed_since(date(2025, 12, 15), date(2026, 1, 1)) == 0
