"""Unit tests for `settle.load` — markdown / csv / provenance writers."""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from settle.domain import Chain, Month, MonthlyPnL, Period, VenueRevenue
from settle.load import write_settlement
from settle.load.csv import write_csv, write_venues_csv
from settle.load.markdown import _fmt_usd, render_markdown, write_markdown
from settle.load.provenance import render_provenance, write_provenance


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _sample_pnl() -> MonthlyPnL:
    period = Period(date(2026, 3, 1), date(2026, 3, 31), pin_blocks={Chain.ETHEREUM: 24971074})
    venue = VenueRevenue(
        venue_id="V1",
        label="Maple syrupUSDC",
        value_som=Decimal("604_000_000.123456"),
        value_eom=Decimal("610_500_000.654321"),
        period_inflow=Decimal("0"),
        revenue=Decimal("6_500_000.530865"),
    )
    sky = Decimal("123456.78")
    agent = Decimal("234567.89")
    prime_rev = Decimal("6_500_000.530865")
    return MonthlyPnL(
        prime_id="obex",
        month=Month(2026, 3),
        period=period,
        sky_revenue=sky,
        agent_rate=agent,
        prime_agent_revenue=prime_rev,
        monthly_pnl=prime_rev + agent - sky,
        venue_breakdown=[venue],
        pin_blocks_som={Chain.ETHEREUM: 24700000},
    )


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def test_fmt_usd_positive():
    assert _fmt_usd(Decimal("1234567.89")) == "$1,234,567.89"


def test_fmt_usd_negative():
    assert _fmt_usd(Decimal("-99.50")) == "-$99.50"


def test_fmt_usd_zero():
    assert _fmt_usd(Decimal("0")) == "$0.00"


def test_render_markdown_contains_headline():
    md = render_markdown(_sample_pnl())
    assert "# OBEX — Monthly settlement 2026-03" in md
    # New headline shape: total revenue + components, no monthly_pnl row.
    assert "prime_agent_total_revenue" in md
    assert "prime_agent_revenue" in md
    assert "agent_rate" in md
    assert "distribution_rewards" in md
    assert "sky_revenue" in md
    # The netted monthly_pnl is no longer reported in the markdown headline
    # (it stays in provenance.json for audit, see test_provenance_*).
    assert "**monthly_pnl**" not in md


def test_render_markdown_contains_pin_blocks():
    md = render_markdown(_sample_pnl())
    assert "24700000" in md
    assert "24971074" in md
    assert "ethereum" in md


def test_render_markdown_contains_venue_row():
    md = render_markdown(_sample_pnl())
    assert "V1" in md
    assert "Maple syrupUSDC" in md
    assert "$604,000,000.12" in md


def test_render_markdown_skips_venue_section_when_empty():
    pnl = _sample_pnl()
    pnl_no_venues = type(pnl)(
        prime_id=pnl.prime_id, month=pnl.month, period=pnl.period,
        sky_revenue=Decimal("0"), agent_rate=Decimal("0"),
        prime_agent_revenue=Decimal("0"), monthly_pnl=Decimal("0"),
        venue_breakdown=[], pin_blocks_som=pnl.pin_blocks_som,
    )
    md = render_markdown(pnl_no_venues)
    assert "Per-venue breakdown" not in md


def test_write_markdown_creates_file(tmp_path: Path):
    dest = tmp_path / "deep" / "pnl.md"  # parent doesn't exist yet
    out = write_markdown(_sample_pnl(), dest)
    assert out == dest
    assert dest.exists()
    assert dest.read_text().startswith("# OBEX")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def test_write_csv_headline_format(tmp_path: Path):
    dest = tmp_path / "pnl.csv"
    write_csv(_sample_pnl(), dest)
    with dest.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    row = rows[0]
    assert row["prime_id"] == "obex"
    assert row["month"] == "2026-03"
    assert row["n_days"] == "31"
    # Decimal precision preserved (no float rounding)
    assert row["sky_revenue"] == "123456.78"
    # monthly_pnl is no longer in the CSV (it's still in provenance.json).
    assert "monthly_pnl" not in row
    # New columns: distribution_rewards (default 0) + the bold-line total.
    assert row["distribution_rewards"] == "0"
    sample = _sample_pnl()
    assert row["prime_agent_total_revenue"] == str(sample.prime_agent_total_revenue)


def test_write_venues_csv_emits_per_venue_rows(tmp_path: Path):
    dest = tmp_path / "venues.csv"
    out = write_venues_csv(_sample_pnl(), dest)
    assert out == dest
    with dest.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["venue_id"] == "V1"
    assert rows[0]["label"] == "Maple syrupUSDC"


def test_write_venues_csv_returns_none_when_no_venues(tmp_path: Path):
    pnl = _sample_pnl()
    pnl_no_venues = type(pnl)(
        prime_id=pnl.prime_id, month=pnl.month, period=pnl.period,
        sky_revenue=Decimal("0"), agent_rate=Decimal("0"),
        prime_agent_revenue=Decimal("0"), monthly_pnl=Decimal("0"),
        venue_breakdown=[], pin_blocks_som=pnl.pin_blocks_som,
    )
    out = write_venues_csv(pnl_no_venues, tmp_path / "venues.csv")
    assert out is None
    assert not (tmp_path / "venues.csv").exists()


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def test_render_provenance_records_pin_blocks_and_results():
    p = render_provenance(_sample_pnl(), sources={"debt": "DuneDebtSource"})
    assert p["prime_id"] == "obex"
    assert p["month"] == "2026-03"
    assert p["pin_blocks_eom"] == {"ethereum": 24971074}
    assert p["pin_blocks_som"] == {"ethereum": 24700000}
    assert p["results"]["sky_revenue"] == "123456.78"
    assert p["results"]["monthly_pnl"] == str(_sample_pnl().monthly_pnl)
    assert p["sources"] == {"debt": "DuneDebtSource"}
    assert len(p["venue_breakdown"]) == 1


def test_render_provenance_carries_settle_version():
    from settle import __version__
    p = render_provenance(_sample_pnl())
    assert p["settle_version"] == __version__


def test_write_provenance_emits_valid_json(tmp_path: Path):
    dest = tmp_path / "provenance.json"
    write_provenance(_sample_pnl(), dest)
    parsed = json.loads(dest.read_text())
    assert parsed["prime_id"] == "obex"


# ---------------------------------------------------------------------------
# Top-level writer
# ---------------------------------------------------------------------------

def test_write_settlement_emits_all_artifacts(tmp_path: Path):
    written = write_settlement(_sample_pnl(), tmp_path, sources={"debt": "Mock"})
    assert set(written) == {"markdown", "csv", "provenance", "venues_csv"}
    assert (tmp_path / "pnl.md").exists()
    assert (tmp_path / "pnl.csv").exists()
    assert (tmp_path / "venues.csv").exists()
    assert (tmp_path / "provenance.json").exists()


def test_write_settlement_no_venues_csv_when_empty(tmp_path: Path):
    pnl = _sample_pnl()
    pnl_no_v = type(pnl)(
        prime_id=pnl.prime_id, month=pnl.month, period=pnl.period,
        sky_revenue=Decimal("0"), agent_rate=Decimal("0"),
        prime_agent_revenue=Decimal("0"), monthly_pnl=Decimal("0"),
        venue_breakdown=[], pin_blocks_som=pnl.pin_blocks_som,
    )
    written = write_settlement(pnl_no_v, tmp_path)
    assert "venues_csv" not in written
    assert not (tmp_path / "venues.csv").exists()


def test_write_settlement_creates_nested_dir(tmp_path: Path):
    deep = tmp_path / "agents" / "obex" / "settlements" / "2026-03"
    write_settlement(_sample_pnl(), deep)
    assert deep.exists()
    assert (deep / "pnl.md").exists()
