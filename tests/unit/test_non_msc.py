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
    ("income:psm_jar", "2026-05-14", None, 10_644_203.21),   # burn landed in May
    ("income:stability_fee", "ETH-A", None, 1_101_413.66),
    ("income:stability_fee", "ETH-C", None, 2_037_068.96),
    ("income:stability_fee", "RWA002-A", None, 201_032.40),
    ("income:liq_owe", "liquidation owe (takes)", None, 2_539_001.67),
    ("income:liq_due", "liquidation due (barks)", None, 2_249_174.24),
    ("income:surplus_return", "2026-05-05", None, 172_545.00),
    ("income:rwa_void", "RWA jars (void)", None, 0.0),
    ("expense:susds_drip", "sUSDS SSR (gross, all holders)", None, 18_107_793.46),
    ("expense:susds_prime", "spark_alm", None, 5_799_596.59),
    ("expense:dsr_drip", "DSR (pot)", None, 249_020.80),
    ("expense:stusds_drip", "stUSDS", None, 1_061_298.20),
    ("expense:liq_coin", "keeper incentives (kicks + redos)", None, 6_539.00),
    ("expense:vest", "vest (gross suckable)", None, 161_048.00),
)


@pytest.fixture()
def fake_query(monkeypatch):
    calls = {}

    def _fake(sql_path, params, pin_block, performance=None):
        calls.update(params=params, pin_block=pin_block)
        return calls["df"]

    monkeypatch.setattr(dune, "execute_query", _fake)
    return calls


def test_month_bounds():
    import datetime as _dt
    assert _month_bounds(Month(2026, 5)) == (_dt.date(2026, 5, 1), _dt.date(2026, 6, 1))
    # Year boundary.
    assert _month_bounds(Month(2026, 12)) == (_dt.date(2026, 12, 1), _dt.date(2027, 1, 1))


def test_bucketing_and_totals(fake_query):
    fake_query["df"] = _MAY_ROWS
    r = compute_non_msc_monthly(Month(2026, 5), pin_block=23_500_000)
    assert r.psm_jar_income == Decimal("10644203.21")
    assert r.stability_fee_income == (
        Decimal("1101413.66") + Decimal("2037068.96") + Decimal("201032.40")
    )
    assert r.susds_expense_to_users == Decimal("18107793.46") - Decimal("5799596.59")
    # Liquidation revenue is the realized penalty: Σowe − Σdue.
    assert r.liq_owe == Decimal("2539001.67")
    assert r.liq_due == Decimal("2249174.24")
    assert r.liq_revenue == Decimal("2539001.67") - Decimal("2249174.24")
    assert r.surplus_return_income == Decimal("172545.00")
    assert r.rwa_jar_void == Decimal("0.0")
    assert r.total_income == (
        r.psm_jar_income + r.stability_fee_income + r.rwa_jar_void
        + r.liq_revenue + r.surplus_return_income
    )
    # GROSS sUSDS in the total — the prime-held slice is offset by BR inside
    # MSC; deducting it here would double-count at the sky_total level.
    # Expense also carries liquidation keeper incentives + gross vest.
    assert r.total_expense == (
        Decimal("18107793.46") + Decimal("249020.80") + Decimal("1061298.20")
        + Decimal("6539.00") + Decimal("161048.00")
    )
    assert r.net_revenue == r.total_income - r.total_expense
    assert not r.warnings
    # The query received the calendar-month window (cash-basis burns).
    assert fake_query["params"]["month_start"] == "2026-05-01"
    assert fake_query["params"]["month_end_excl"] == "2026-06-01"
    assert "burn_end_excl" not in fake_query["params"]


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


def test_two_burns_in_month_both_counted(fake_query):
    """Jan 2026 lands two burns (December's on-slot payment + November's late
    one). Cash basis counts BOTH — the money that landed in January is
    January's income, no unattributed remainder."""
    fake_query["df"] = _rows(
        ("income:psm_jar", "2026-01-08", None, 11_046_889.78),
        ("income:psm_jar", "2026-01-02", None, 9_618_048.74),   # out of order on purpose
        ("expense:susds_drip", "gross", None, 0.0),
        ("expense:dsr_drip", "DSR (pot)", None, 0.0),
        ("expense:stusds_drip", "stUSDS", None, 0.0),
    )
    r = compute_non_msc_monthly(Month(2026, 1), pin_block=1)
    assert r.psm_jar_income == Decimal("11046889.78") + Decimal("9618048.74")
    assert [b["date"] for b in r.jar_burns] == ["2026-01-02", "2026-01-08"]   # sorted
    assert not any("NOT attributed" in w for w in r.warnings)


def test_unknown_stream_raises(fake_query):
    fake_query["df"] = _rows(("income:mystery", "x", None, 1.0))
    with pytest.raises(ValueError, match="unknown stream"):
        compute_non_msc_monthly(Month(2026, 5), pin_block=1)


def test_render_summary_shape(fake_query):
    fake_query["df"] = _MAY_ROWS
    r = compute_non_msc_monthly(Month(2026, 5), pin_block=23_500_000)
    out = render_summary(r)
    assert "# NON_MSC — 2026-05" in out
    assert "| PSM | LitePSM jar burn (2026-05-14) | 10,644,203.21 |" in out
    assert "| Savings | — of which: prime-held, spark_alm (offset by BR in MSC) | 5,799,596.59 |" in out
    assert "| Savings | — of which: non-prime users (informational) | 12,308,196.87 |" in out
    # RWA ilks land in the Legacy RWA section, core ilks in Crypto Vaults.
    assert "| Crypto Vaults | stability fee ETH-C | 2,037,068.96 |" in out
    assert "| Legacy RWA | stability fee RWA002-A | 201,032.40 |" in out
    # Liquidation revenue shows its owe/due components; keeper incentives + vest
    # appear on the expense side.
    assert "liquidation revenue (Σowe 2,539,001.67 − Σdue 2,249,174.24) | 289,827.43 |" in out
    assert "| Other | surplus return (2026-05-05) | 172,545.00 |" in out
    assert "| Liquidations | keeper incentives (Σ coin, kicks + redos) | 6,539.00 |" in out
    assert "| Vest | gross suckable payouts | 161,048.00 |" in out
    assert "**non-MSC net revenue**" in out


def test_rwa_void_tripwire_warns(fake_query):
    """A non-zero RWA jar void is abnormal — it must warn loudly."""
    fake_query["df"] = _rows(
        ("income:psm_jar", "2026-05-14", None, 10_000.0),
        ("income:rwa_void", "RWA jars (void)", None, 42_000.0),
        ("expense:susds_drip", "gross", None, 0.0),
        ("expense:dsr_drip", "DSR (pot)", None, 0.0),
        ("expense:stusds_drip", "stUSDS", None, 0.0),
    )
    r = compute_non_msc_monthly(Month(2026, 5), pin_block=1)
    assert r.rwa_jar_void == Decimal("42000.0")
    assert any("RWA jar void" in w for w in r.warnings)
