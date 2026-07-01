"""Unit tests for `settle.normalize.debt` using a mock source."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from settle.domain import Chain, Month, Period
from settle.domain.config import load_prime
from settle.normalize.debt import get_debt_timeseries
from settle.validation import SchemaError

from replay.mock_sources import MockDebtSource


@dataclass
class _MockBlockResolver:
    """In-memory ``IBlockResolver`` — maps each calendar date to a fixed block."""

    blocks_by_date: dict[date, int] = field(default_factory=dict)

    def block_at_or_before(self, chain: str, anchor_utc: datetime) -> int:
        return self.blocks_by_date.get(anchor_utc.date(), 0)


def _obex(config_dir: Path):
    return load_prime(config_dir / "obex.yaml")


def _period() -> Period:
    return Period.from_month(Month(2026, 4), pin_blocks={Chain.ETHEREUM: 24971074})


def test_get_debt_timeseries_passes_prime_args_to_source(config_dir: Path):
    """Normalize must use prime.start_date (not period.start) so we have full
    history for SoM/EoM slicing in Compute."""
    src = MockDebtSource(pd.DataFrame({
        "block_date": [date(2025, 11, 18)],
        "daily_dart": [50_000_000.0],
        "cum_debt": [50_000_000.0],
    }))
    obex = _obex(config_dir)

    result = get_debt_timeseries(obex, _period(), source=src)

    # Source was called with prime.start_date and the period's pin block
    assert len(src.calls) == 1
    ilk, start, pin = src.calls[0]
    assert ilk == obex.ilk_bytes32
    assert start == obex.start_date  # 2025-11-17
    assert pin == 24971074

    # Returned the source DataFrame unchanged
    assert len(result) == 1
    assert result.iloc[0].cum_debt == 50_000_000.0


def test_get_debt_timeseries_rejects_period_without_eth_pin(config_dir: Path):
    obex = _obex(config_dir)
    period_no_pin = Period.from_month(Month(2026, 4), pin_blocks={})  # no pin
    with pytest.raises(ValueError, match="pin_block"):
        get_debt_timeseries(obex, period_no_pin, source=MockDebtSource())


def test_get_debt_timeseries_rejects_malformed_source_output(config_dir: Path):
    """If a Source returns the wrong shape, Normalize raises at the boundary."""
    bad_src = MockDebtSource(pd.DataFrame({"wrong_column": [1, 2, 3]}))
    obex = _obex(config_dir)
    with pytest.raises(SchemaError, match="missing required columns"):
        get_debt_timeseries(obex, _period(), source=bad_src)


def test_get_debt_timeseries_accepts_empty_dataframe(config_dir: Path):
    """A prime with no frobs yet (e.g. pre-launch) is valid — empty result OK."""
    obex = _obex(config_dir)
    result = get_debt_timeseries(obex, _period(), source=MockDebtSource())
    assert len(result) == 0


def test_get_debt_timeseries_scales_art_by_daily_ilk_rate(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch,
):
    """With a ``block_resolver`` supplied, each day's ``cum_debt`` is
    ``Art_d × rate_d / 1e27``. Verifies the production path: even when
    ``Art`` is flat, a rising rate produces a non-zero ``daily_dart``
    (the daily interest-accrual line)."""
    src = MockDebtSource(pd.DataFrame({
        "block_date": [date(2026, 4, 1)],
        "daily_dart": [Decimal("100_000_000")],
        "cum_debt":   [Decimal("100_000_000")],   # raw Art (wad units)
    }))
    obex = _obex(config_dir)

    period = Period(
        start=date(2026, 4, 1), end=date(2026, 4, 3),
        pin_blocks={Chain.ETHEREUM: 24971074},
    )
    resolver = _MockBlockResolver({
        date(2026, 4, 1): 1001,
        date(2026, 4, 2): 1002,
        date(2026, 4, 3): 1003,
    })
    # rate index = 1.00 → 1.01 → 1.02 (in ray units = ×1e27).
    rate_by_block = {
        1001: 10**27,
        1002: int(Decimal("1.01") * 10**27),
        1003: int(Decimal("1.02") * 10**27),
    }
    from settle.extract import rpc as _rpc
    monkeypatch.setattr(
        _rpc, "ilk_rate",
        lambda chain, vat, ilk, block: rate_by_block[block],
    )

    out = get_debt_timeseries(obex, period, source=src, block_resolver=resolver)

    assert len(out) == 3
    # cum_debt scales by per-day rate; daily_dart is the day-on-day Δ
    # (a $100M starting balance plus 1% / 1% rate accrual the next two days).
    assert out.iloc[0]["cum_debt"]  == Decimal("100000000.00")
    assert out.iloc[1]["cum_debt"]  == Decimal("101000000.00")
    assert out.iloc[2]["cum_debt"]  == Decimal("102000000.00")
    assert out.iloc[0]["daily_dart"] == Decimal("100000000.00")
    assert out.iloc[1]["daily_dart"] == Decimal("1000000.00")
    assert out.iloc[2]["daily_dart"] == Decimal("1000000.00")


def test_get_debt_timeseries_warns_when_no_resolver_passed(
    config_dir: Path, caplog: pytest.LogCaptureFixture,
):
    """The no-resolver path returns raw Art (not USDS) and emits a warning so
    a forgetful caller sees the footgun. Locks in the safeguard introduced
    after the silent-fallback review finding."""
    src = MockDebtSource(pd.DataFrame({
        "block_date": [date(2026, 4, 1)],
        "daily_dart": [Decimal("50_000_000")],
        "cum_debt":   [Decimal("50_000_000")],
    }))
    obex = _obex(config_dir)
    with caplog.at_level(logging.WARNING, logger="settle.normalize.debt"):
        out = get_debt_timeseries(obex, _period(), source=src)
    # Returned series is the raw sparse frame, unmodified.
    assert out.iloc[0]["cum_debt"] == Decimal("50_000_000")
    # The footgun warning fired.
    assert any(
        "without block_resolver" in r.message for r in caplog.records
    ), f"expected warning, got: {[r.message for r in caplog.records]}"


def test_get_debt_timeseries_agent_rate_only_prime_returns_zero_series(config_dir: Path):
    """``ilk_bytes32 is None`` (Keel/Skybase) → all-zero single-row series,
    no source query. Non-empty so ``compute_sky_revenue``'s
    ``require_non_empty(debt)`` guard passes with a genuine zero BR base."""
    keel = load_prime(config_dir / "keel.yaml")
    src = MockDebtSource()
    result = get_debt_timeseries(keel, _period(), source=src)
    assert src.calls == []          # Dune never queried
    assert len(result) == 1
    assert result.iloc[0].cum_debt == 0
    assert result.iloc[0].daily_dart == 0
    # Load-bearing: cum_at_or_before carries this row forward from day 1 of
    # the period, so the BR base reads 0 on every day — a block_date outside
    # the period would silently desync the daily loop.
    assert result.iloc[0]["block_date"] == _period().start
