"""Unit tests for ``_mid_period_sde_blend`` in ``scripts/build_monthly_report.py``.

The helper computes the CoF-bearing time-weighted avg_value when an SDE
designation ends mid-period. Two contributions are blended:

* During SDE-active days [period_start, sde_end] (inclusive both ends):
  Grove held only the excess above ``cap_usd`` — that slice bears CoF.
  ``cap_usd=None`` (fixed SDE) → zero excess; only the post-SDE term
  contributes.

* During non-SDE days (sde_end, period_end] (strictly after sde_end):
  Grove holds the full remaining position at value_eom; entire balance
  bears CoF.

Day-count convention matches the pipeline gate (``current > end_date``
→ SDE-inactive), so ``end_date`` itself is the LAST SDE-active day.

The helper returns ``None`` when ``sde_end == period_end`` (override is
a no-op for full-period SDE windows) or when ``sde_end`` falls outside
the period entirely.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

# build_monthly_report.py lives in scripts/, not an installable package.
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

from build_monthly_report import _mid_period_sde_blend  # noqa: E402


def test_returns_none_when_sde_end_equals_period_end():
    """sde_end == period_end means SDE was active for the entire period —
    override is a no-op (non_sde_days_n would be 0, sde_days_n would equal
    total_days_n). Helper returns None to signal "no blend needed"."""
    out = _mid_period_sde_blend(
        value_som=Decimal("500_000_000"),
        value_eom=Decimal("450_000_000"),
        cap_usd=Decimal("325_000_000"),
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        sde_end=date(2026, 3, 31),       # full period coverage
    )
    assert out is None


def test_returns_none_when_sde_end_after_period_end():
    """sde_end > period_end: SDE still active after period close — no
    mid-period transition happens this month."""
    out = _mid_period_sde_blend(
        value_som=Decimal("500_000_000"),
        value_eom=Decimal("450_000_000"),
        cap_usd=Decimal("325_000_000"),
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        sde_end=date(2026, 4, 15),
    )
    assert out is None


def test_returns_none_when_sde_end_before_period_start():
    """sde_end < period_start: SDE ended before this period began. The
    SDE loader normally drops such entries upstream, but the helper must
    also be defensive against passing a stale entry."""
    out = _mid_period_sde_blend(
        value_som=Decimal("500_000_000"),
        value_eom=Decimal("450_000_000"),
        cap_usd=Decimal("325_000_000"),
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        sde_end=date(2026, 2, 15),
    )
    assert out is None


def test_jaaa_march_2026_capped_sde_blend():
    """E8 JAAA Eth, Mar 2026 — SDE capped at $325M, ended 2026-03-12.

    Per the corrected day-count (end_date is SDE-active):
      SDE days   = Mar 1–12 = 12 days
      Non-SDE    = Mar 13–31 = 19 days
      Total      = 31 days
      grove_excess = max(0, 455M − 325M) = 130M
      new_avg    = (130M × 12 + 128M × 19) / 31 ≈ $128.8M
    """
    out = _mid_period_sde_blend(
        value_som=Decimal("455_000_000"),
        value_eom=Decimal("128_000_000"),
        cap_usd=Decimal("325_000_000"),
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        sde_end=date(2026, 3, 12),
    )
    assert out is not None
    new_avg, sde_days_n, non_sde_days_n, total_days_n, grove_excess = out
    assert sde_days_n == 12
    assert non_sde_days_n == 19
    assert total_days_n == 31
    assert grove_excess == Decimal("130_000_000")
    expected = (Decimal("130_000_000") * 12 + Decimal("128_000_000") * 19) / 31
    assert new_avg == expected
    # Sanity bound
    assert Decimal("128_000_000") < new_avg < Decimal("130_000_000")


def test_sde_end_on_first_day_of_period():
    """Edge: SDE ends on period_start itself — 1 SDE day, rest non-SDE."""
    out = _mid_period_sde_blend(
        value_som=Decimal("500_000_000"),
        value_eom=Decimal("400_000_000"),
        cap_usd=Decimal("300_000_000"),
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        sde_end=date(2026, 3, 1),
    )
    assert out is not None
    new_avg, sde_days_n, non_sde_days_n, total_days_n, grove_excess = out
    assert sde_days_n == 1
    assert non_sde_days_n == 30
    assert total_days_n == 31
    assert grove_excess == Decimal("200_000_000")    # 500M − 300M
    expected = (Decimal("200_000_000") * 1 + Decimal("400_000_000") * 30) / 31
    assert new_avg == expected


def test_sde_end_on_penultimate_day_of_period():
    """Edge: SDE ends on the day BEFORE period_end — 30 SDE days, 1 non-SDE."""
    out = _mid_period_sde_blend(
        value_som=Decimal("500_000_000"),
        value_eom=Decimal("400_000_000"),
        cap_usd=Decimal("300_000_000"),
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        sde_end=date(2026, 3, 30),
    )
    assert out is not None
    _new_avg, sde_days_n, non_sde_days_n, total_days_n, _grove_excess = out
    assert sde_days_n == 30
    assert non_sde_days_n == 1
    assert total_days_n == 31
    assert sde_days_n + non_sde_days_n == total_days_n


def test_fixed_sde_has_zero_grove_excess():
    """When ``cap_usd=None`` (fixed SDE, 100% to Sky), Grove has no excess
    during the SDE-active window — only the post-SDE term contributes."""
    out = _mid_period_sde_blend(
        value_som=Decimal("100_000_000"),
        value_eom=Decimal("50_000_000"),
        cap_usd=None,                       # fixed SDE
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        sde_end=date(2026, 3, 15),
    )
    assert out is not None
    new_avg, sde_days_n, non_sde_days_n, _total_days_n, grove_excess = out
    assert grove_excess == Decimal("0")
    assert sde_days_n == 15
    assert non_sde_days_n == 16
    # new_avg = (0 × 15 + 50M × 16) / 31
    assert new_avg == (Decimal("50_000_000") * 16) / 31


def test_value_som_below_cap_clamps_excess_to_zero():
    """If the prime's SoM value is BELOW the cap (entire position covered
    by the SDE), grove_excess is 0 — no CoF burden during SDE days."""
    out = _mid_period_sde_blend(
        value_som=Decimal("200_000_000"),
        value_eom=Decimal("180_000_000"),
        cap_usd=Decimal("325_000_000"),     # cap above value
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        sde_end=date(2026, 3, 15),
    )
    assert out is not None
    new_avg, sde_days_n, non_sde_days_n, _total_days_n, grove_excess = out
    assert grove_excess == Decimal("0")
    # Only post-SDE term contributes
    assert new_avg == (Decimal("180_000_000") * non_sde_days_n) / 31
    assert sde_days_n == 15
    assert non_sde_days_n == 16


def test_day_count_invariant_holds_for_february():
    """sde_days_n + non_sde_days_n == total_days_n on a 28-day month too."""
    out = _mid_period_sde_blend(
        value_som=Decimal("100_000_000"),
        value_eom=Decimal("80_000_000"),
        cap_usd=Decimal("50_000_000"),
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
        sde_end=date(2026, 2, 14),
    )
    assert out is not None
    _new_avg, sde_days_n, non_sde_days_n, total_days_n, _grove_excess = out
    assert sde_days_n == 14                 # Feb 1–14 inclusive
    assert non_sde_days_n == 14             # Feb 15–28
    assert total_days_n == 28
    assert sde_days_n + non_sde_days_n == total_days_n


def test_day_count_invariant_holds_for_30_day_month():
    out = _mid_period_sde_blend(
        value_som=Decimal("100_000_000"),
        value_eom=Decimal("80_000_000"),
        cap_usd=Decimal("50_000_000"),
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        sde_end=date(2026, 4, 10),
    )
    assert out is not None
    _new_avg, sde_days_n, non_sde_days_n, total_days_n, _grove_excess = out
    assert sde_days_n == 10                 # Apr 1–10 inclusive
    assert non_sde_days_n == 20             # Apr 11–30
    assert total_days_n == 30
    assert sde_days_n + non_sde_days_n == total_days_n
