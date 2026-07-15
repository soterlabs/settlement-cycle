"""Unit tests for the non_msc report (bucketing, attribution rules, rendering)."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

import settle.extract.dune as dune
from settle.compute.non_msc import (
    NonMscMonthly,
    _month_bounds,
    compute_non_msc_monthly,
    render_summary,
)
from settle.domain import Month


def _rows(*rows):
    return pd.DataFrame(rows, columns=["stream", "label", "event_date", "amount"])


_MAY_ROWS = _rows(
    ("income:psm_jar", "2026-06-11", None, 10_644_203.21),
    ("income:stability_fee", "ETH-A", None, 1_101_413.66),
    ("income:stability_fee", "ETH-C", None, 2_037_068.96),
    ("expense:susds_drip", "sUSDS SSR (gross, all holders)", None, 18_107_793.46),
    ("expense:susds_prime", "spark_alm", None, 5_799_596.59),
    ("expense:dsr_drip", "DSR (pot)", None, 249_020.80),
    ("expense:stusds_drip", "stUSDS", None, 1_061_298.20),
)


@pytest.fixture()
def fake_query(monkeypatch):
    calls = {}

    def _fake(sql_path, params, pin_block, performance=None):
        calls.update(params=params, pin_block=pin_block)
        return calls["df"]

    monkeypatch.setattr(dune, "execute_query", _fake)
    return calls


def test_month_bounds_and_burn_window():
    assert _month_bounds(Month(2026, 5)) == (
        __import__("datetime").date(2026, 5, 1),
        __import__("datetime").date(2026, 6, 1),
        __import__("datetime").date(2026, 7, 1),
    )
    # Year boundaries.
    assert _month_bounds(Month(2026, 12))[1:] == (
        __import__("datetime").date(2027, 1, 1),
        __import__("datetime").date(2027, 2, 1),
    )
    assert _month_bounds(Month(2026, 11))[2] == __import__("datetime").date(2027, 1, 1)


def test_bucketing_and_totals(fake_query):
    fake_query["df"] = _MAY_ROWS
    r = compute_non_msc_monthly(Month(2026, 5), pin_block=23_500_000)
    assert r.psm_jar_income == Decimal("10644203.21")
    assert r.stability_fee_income == Decimal("1101413.66") + Decimal("2037068.96")
    assert r.susds_expense_net == Decimal("18107793.46") - Decimal("5799596.59")
    assert r.total_income == r.psm_jar_income + r.stability_fee_income
    assert r.total_expense == r.susds_expense_net + Decimal("249020.80") + Decimal("1061298.20")
    assert r.net_revenue == r.total_income - r.total_expense
    assert not r.warnings
    # The query received the calendar window, not pin-derived dates.
    assert fake_query["params"]["month_start"] == "2026-05-01"
    assert fake_query["params"]["burn_end_excl"] == "2026-07-01"


def test_missing_burn_warns_not_crashes(fake_query):
    fake_query["df"] = _rows(
        ("income:stability_fee", "ETH-A", None, 100.0),
        ("expense:susds_drip", "gross", None, 50.0),
        ("expense:dsr_drip", "DSR (pot)", None, 1.0),
        ("expense:stusds_drip", "stUSDS", None, 2.0),
    )
    r = compute_non_msc_monthly(Month(2026, 6), pin_block=1)
    assert r.psm_jar_income == 0
    assert any("no jar burn" in w for w in r.warnings)


def test_two_burns_in_window_first_only_attributed(fake_query):
    """The 2026-01 window shape: two burns after Dec month-end. Per the
    methodology doc's LITERAL rule, only the FIRST burn is that month's
    income; the extra burn is surfaced loudly but not attributed."""
    fake_query["df"] = _rows(
        ("income:psm_jar", "2026-01-08", None, 11_046_889.78),
        ("income:psm_jar", "2026-01-02", None, 9_618_048.74),   # out of order on purpose
        ("expense:susds_drip", "gross", None, 0.0),
        ("expense:dsr_drip", "DSR (pot)", None, 0.0),
        ("expense:stusds_drip", "stUSDS", None, 0.0),
    )
    r = compute_non_msc_monthly(Month(2025, 12), pin_block=1)
    assert r.psm_jar_income == Decimal("9618048.74")            # first burn only
    assert [b["date"] for b in r.jar_burns] == ["2026-01-02"]
    assert any("NOT attributed" in w and "2026-01-08" in w for w in r.warnings)


def test_unknown_stream_raises(fake_query):
    fake_query["df"] = _rows(("income:mystery", "x", None, 1.0))
    with pytest.raises(ValueError, match="unknown stream"):
        compute_non_msc_monthly(Month(2026, 5), pin_block=1)


def test_render_summary_shape(fake_query):
    fake_query["df"] = _MAY_ROWS
    r = compute_non_msc_monthly(Month(2026, 5), pin_block=23_500_000)
    out = render_summary(r)
    assert "# NON_MSC — 2026-05" in out
    assert "| PSM/Coinbase jar burn (2026-06-11) | 10,644,203.21 |" in out
    assert "| less: prime-held sUSDS SSR — spark_alm (MSC-accounted) | -5,799,596.59 |" in out
    assert "| sUSDS SSR to non-prime users | 12,308,196.87 |" in out
    assert "**non-MSC net revenue**" in out
