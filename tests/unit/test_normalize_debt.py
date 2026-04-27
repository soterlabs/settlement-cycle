"""Unit tests for `settle.normalize.debt` using a mock source."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from settle.domain import Chain, Month, Period
from settle.domain.config import load_prime
from settle.normalize.debt import get_debt_timeseries
from settle.validation import SchemaError

from ..fixtures.mock_sources import MockDebtSource


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
