"""Unit tests for `settle.compute._helpers`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from settle.compute._helpers import (
    cum_at_or_before,
    daily_compounding_factor,
    ssr_at_or_before,
)


# --- daily_compounding_factor ---------------------------------------------

def test_daily_factor_zero_apy():
    assert daily_compounding_factor(Decimal("0")) == Decimal("0.0")


def test_daily_factor_5_pct_apy():
    """((1.05)^(1/365) - 1) ≈ 0.00013368... — verified independently."""
    f = daily_compounding_factor(Decimal("0.05"))
    assert Decimal("0.000133") < f < Decimal("0.000135")


def test_daily_factor_compounds_to_apy_over_365_days():
    """Compounding the factor 365× should reproduce APY (within float precision)."""
    apy = Decimal("0.045")
    f = daily_compounding_factor(apy)
    grown = (1 + float(f)) ** 365 - 1
    assert abs(grown - 0.045) < 1e-10


# --- cum_at_or_before ------------------------------------------------------

def _ts():
    return pd.DataFrame({
        "block_date": [date(2025, 11, 17), date(2025, 12, 1), date(2026, 2, 2)],
        "cum_balance": [21_000_000.0, 25_000_000.0, 25_442_327.0],
    })


def test_cum_at_or_before_returns_latest_at_target():
    assert cum_at_or_before(_ts(), "cum_balance", date(2025, 11, 17)) == Decimal("21000000.0")
    assert cum_at_or_before(_ts(), "cum_balance", date(2025, 12, 31)) == Decimal("25000000.0")
    assert cum_at_or_before(_ts(), "cum_balance", date(2026, 2, 2)) == Decimal("25442327.0")
    assert cum_at_or_before(_ts(), "cum_balance", date(2026, 4, 1)) == Decimal("25442327.0")


def test_cum_at_or_before_returns_zero_for_pre_history():
    assert cum_at_or_before(_ts(), "cum_balance", date(2025, 1, 1)) == Decimal("0")


def test_cum_at_or_before_handles_empty_dataframe():
    empty = pd.DataFrame({"block_date": [], "cum_balance": []})
    assert cum_at_or_before(empty, "cum_balance", date(2026, 4, 1)) == Decimal("0")


def test_cum_at_or_before_handles_none():
    assert cum_at_or_before(None, "cum_balance", date(2026, 4, 1)) == Decimal("0")


def test_cum_at_or_before_with_decimal_value_column():
    """Production Dune sources emit `Decimal` (not `float`) in `cum_*` columns
    after fix I4. Ensure carry-forward still works when values are Decimals,
    not floats — locks in the production-source dtype contract."""
    df = pd.DataFrame({
        "block_date":  [date(2025, 11, 17), date(2026, 2, 2)],
        "cum_balance": [Decimal("21000000"), Decimal("21442327.123456789")],
    })
    # High-precision value preserved end-to-end (no float intermediate).
    out = cum_at_or_before(df, "cum_balance", date(2026, 3, 1))
    assert out == Decimal("21442327.123456789")
    assert isinstance(out, Decimal)


def test_cum_at_or_before_unsorted_input():
    """If the source returned rows out of date order, ``idxmax`` still finds the
    row with the largest date ≤ target."""
    df = pd.DataFrame({
        "block_date":  [date(2026, 3, 30), date(2025, 11, 17), date(2026, 2, 2)],
        "cum_balance": [25_000_000.0,      21_000_000.0,        22_000_000.0],
    })
    assert cum_at_or_before(df, "cum_balance", date(2026, 3, 1)) == Decimal("22000000.0")
    assert cum_at_or_before(df, "cum_balance", date(2026, 4, 1)) == Decimal("25000000.0")


# --- ssr_at_or_before ------------------------------------------------------

def _ssr():
    return pd.DataFrame({
        "effective_date": [date(2025, 12, 2), date(2025, 12, 16), date(2026, 3, 9)],
        "ssr_apy":        [0.0425,            0.0400,             0.0375],
    })


def test_ssr_at_or_before_returns_latest_change():
    assert ssr_at_or_before(_ssr(), date(2025, 12, 2)) == Decimal("0.0425")
    assert ssr_at_or_before(_ssr(), date(2025, 12, 31)) == Decimal("0.04")
    assert ssr_at_or_before(_ssr(), date(2026, 3, 9)) == Decimal("0.0375")
    assert ssr_at_or_before(_ssr(), date(2026, 4, 30)) == Decimal("0.0375")


def test_ssr_at_or_before_raises_when_no_prior_change():
    with pytest.raises(ValueError, match="No SSR change at or before"):
        ssr_at_or_before(_ssr(), date(2025, 11, 1))


def test_ssr_at_or_before_raises_when_history_empty():
    empty = pd.DataFrame({"effective_date": [], "ssr_apy": []})
    with pytest.raises(ValueError, match="empty"):
        ssr_at_or_before(empty, date(2026, 3, 1))
