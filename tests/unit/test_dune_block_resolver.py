"""Unit tests for DuneBlockResolver — bulk date↔block mapping."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest


def _make_resolver(monkeypatch, df_rows):
    """Build a DuneBlockResolver with a synthetic Dune response."""
    from settle.normalize.sources import dune_block_resolver as dbr

    df = pd.DataFrame(df_rows)
    monkeypatch.setattr(dbr, "execute_query", lambda *a, **kw: df)
    return dbr.DuneBlockResolver(
        chain="ethereum",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 31),
        pin_block=24781026,
    )


def test_block_at_or_before_eod_anchor(monkeypatch):
    """End-of-day anchor → returns that day's max block."""
    resolver = _make_resolver(monkeypatch, [
        {"block_date": date(2026, 3, 1), "block_number": 24600000},
        {"block_date": date(2026, 3, 2), "block_number": 24607200},
        {"block_date": date(2026, 3, 3), "block_number": 24614400},
    ])
    eod_mar2 = datetime(2026, 3, 2, 23, 59, 59, tzinfo=timezone.utc)
    assert resolver.block_at_or_before("ethereum", eod_mar2) == 24607200


def test_block_at_or_before_midday_snaps_to_day_max(monkeypatch):
    """Mid-day anchor returns the day's max block (slight overshoot is
    documented and acceptable for monthly settlement anchors)."""
    resolver = _make_resolver(monkeypatch, [
        {"block_date": date(2026, 3, 1), "block_number": 24600000},
        {"block_date": date(2026, 3, 2), "block_number": 24607200},
    ])
    midday_mar2 = datetime(2026, 3, 2, 12, 0, 0, tzinfo=timezone.utc)
    assert resolver.block_at_or_before("ethereum", midday_mar2) == 24607200


def test_block_to_date_first_containing_day(monkeypatch):
    """block_to_date finds the day whose max_block ≥ block."""
    resolver = _make_resolver(monkeypatch, [
        {"block_date": date(2026, 3, 1), "block_number": 24600000},
        {"block_date": date(2026, 3, 2), "block_number": 24607200},
        {"block_date": date(2026, 3, 3), "block_number": 24614400},
    ])
    # Block 24605000 is between Mar 1's max (24600000) and Mar 2's max
    # (24607200), so it belongs to Mar 2.
    assert resolver.block_to_date("ethereum", 24605000) == date(2026, 3, 2)
    # Exact match on Mar 2's max → Mar 2.
    assert resolver.block_to_date("ethereum", 24607200) == date(2026, 3, 2)


def test_block_to_date_raises_for_block_after_range(monkeypatch):
    """A block beyond the indexed range raises clearly."""
    resolver = _make_resolver(monkeypatch, [
        {"block_date": date(2026, 3, 1), "block_number": 24600000},
    ])
    with pytest.raises(ValueError, match="after the indexed range"):
        resolver.block_to_date("ethereum", 24700000)


def test_chain_mismatch_raises(monkeypatch):
    """Resolver is bound to one chain at construction; cross-chain query raises."""
    resolver = _make_resolver(monkeypatch, [
        {"block_date": date(2026, 3, 1), "block_number": 24600000},
    ])
    with pytest.raises(ValueError, match="bound to 'ethereum'"):
        resolver.block_at_or_before("base", datetime(2026, 3, 1, tzinfo=timezone.utc))
