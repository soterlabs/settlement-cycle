"""Unit tests for `_cat_a_capital_inflow_timeseries` (Cat A par-stable
external-source allowlist netting).

Validates the polarity-flipped semantics: counterparties IN
``external_sources`` pass through to revenue; everything else nets as capital.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from settle.domain import Chain, Month, Period
from settle.domain.config import load_prime
from settle.normalize.positions import _cat_a_capital_inflow_timeseries

from ..fixtures.mock_sources import MockBalanceSource


def _grove_e15(config_dir: Path):
    grove = load_prime(config_dir / "grove.yaml")
    venue = next(v for v in grove.venues if v.id == "E15")
    return grove, venue


def _eth_period(block: int = 24781026) -> Period:
    return Period.from_month(Month(2026, 3), pin_blocks={Chain.ETHEREUM: block})


def _bytes20(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str.removeprefix("0x")).rjust(20, b"\x00")


def test_cat_a_empty_external_set_nets_full_value_revenue_zero(config_dir: Path):
    """Empty external_sources → every counterparty is internal/capital →
    period_inflow == Δvalue → revenue = 0. The default Grove case today."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    src = MockBalanceSource()
    src.cumulative_df = pd.DataFrame()  # not used by this helper
    cp_internal = _bytes20("0x37305b1cd40574e4c5ce33f8e8306be057fd7341")  # PSM
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 5),   date(2026, 3, 10)],
        "counterparty": [cp_internal,        cp_external],
        "signed_amount":[Decimal("100000"),  Decimal("250000")],
    })

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources=set(),
    )
    assert out["daily_inflow"].sum() == Decimal("350000")
    assert out["cum_inflow"].iloc[-1] == Decimal("350000")


def test_cat_a_external_counterparty_excluded_from_capital(config_dir: Path):
    """When the external counterparty is allowlisted, its row is excluded
    from period_inflow → that flow becomes revenue."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_internal = _bytes20("0x37305b1cd40574e4c5ce33f8e8306be057fd7341")
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 5),   date(2026, 3, 10)],
        "counterparty": [cp_internal,        cp_external],
        "signed_amount":[Decimal("100000"),  Decimal("250000")],
    })

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
    )
    # Only the internal row makes it into capital_inflow.
    assert out["daily_inflow"].sum() == Decimal("100000")


def test_cat_a_short_bytes_counterparty_normalizes_for_membership(
    config_dir: Path,
):
    """Dune varbinary may strip leading zeros — e.g. the zero address can
    arrive as ``b''`` (length 0). Membership against ``Address.value``
    (always 20 bytes) must still match after normalization."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    short_zero = b""                       # zero bytes — leading-zero strip
    full_zero = b"\x00" * 20

    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 5)],
        "counterparty": [short_zero],
        "signed_amount":[Decimal("9000000")],
    })
    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={full_zero},
    )
    # Short-zero counterparty matches the 20-byte allowlist entry → excluded.
    assert out.empty


def test_cat_a_hex_string_counterparty_normalizes(config_dir: Path):
    """Some serializers return varbinary as a ``"0x..."`` hex string."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_hex_external = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    cp_external = _bytes20(cp_hex_external)

    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 10)],
        "counterparty": [cp_hex_external],
        "signed_amount":[Decimal("250000")],
    })
    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
    )
    assert out.empty  # external row excluded → no capital


def test_cat_a_memoryview_counterparty_normalizes(config_dir: Path):
    """Some JSON deserializers produce memoryview for varbinary."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 10)],
        "counterparty": [memoryview(cp_external)],
        "signed_amount":[Decimal("250000")],
    })
    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
    )
    assert out.empty


def test_cat_a_empty_inflow_returns_empty(config_dir: Path):
    """No transfers in the period → empty result."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date": [], "counterparty": [], "signed_amount": [],
    })
    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources=set(),
    )
    assert out.empty
