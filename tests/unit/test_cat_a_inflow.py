"""Unit tests for `_cat_a_capital_inflow_timeseries` (Cat A par-stable
external-yield accounting).

The function returns ``(inflow_ts, external_yield_usd)`` where:
- ``inflow_ts`` contains ALL signed flows (capital + external) — used for
  tw_avg and the ``period_inflow`` in the revenue formula.
- ``external_yield_usd`` accumulates positive inflows from external-source
  counterparties that are NOT matched by a principal-return override.

Revenue identity: actual_revenue = Δvalue − period_inflow = 0 (par-stable)
                  total_revenue  = actual_revenue + external_yield_usd
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


def test_cat_a_empty_external_set_all_flows_are_capital(config_dir: Path):
    """Empty external_sources → every counterparty is capital.
    inflow_ts includes all rows; external_yield_usd is zero."""
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

    ts, ext_yield = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources=set(),
    )
    # All rows in inflow_ts; no external yield.
    assert ts["daily_inflow"].sum() == Decimal("350000")
    assert ts["cum_inflow"].iloc[-1] == Decimal("350000")
    assert ext_yield == Decimal("0")


def test_cat_a_external_counterparty_in_inflow_ts_and_ext_yield(config_dir: Path):
    """External counterparty rows appear in inflow_ts (for tw_avg) AND
    contribute to external_yield_usd (for revenue).  inflow_ts.sum() equals
    all flows; external_yield_usd equals the external portion."""
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

    ts, ext_yield = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
    )
    # Both rows are in the timeseries (for tw_avg).
    assert ts["daily_inflow"].sum() == Decimal("350000")
    # Only the external inflow contributes to external_yield.
    assert ext_yield == Decimal("250000")


def test_cat_a_short_bytes_counterparty_normalizes_for_membership(
    config_dir: Path,
):
    """Dune varbinary may strip leading zeros — e.g. the zero address can
    arrive as ``b''`` (length 0). Membership against the 20-byte allowlist
    must still match after normalization."""
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
    ts, ext_yield = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={full_zero},
    )
    # Row appears in inflow_ts; correctly classified as external yield.
    assert ts["daily_inflow"].sum() == Decimal("9000000")
    assert ext_yield == Decimal("9000000")


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
    ts, ext_yield = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
    )
    assert ts["daily_inflow"].sum() == Decimal("250000")
    assert ext_yield == Decimal("250000")


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
    ts, ext_yield = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
    )
    assert ts["daily_inflow"].sum() == Decimal("250000")
    assert ext_yield == Decimal("250000")


def test_cat_a_principal_return_override_excluded_from_ext_yield(
    config_dir: Path,
):
    """An inflow nominally from an external source whose (date, amount)
    matches a principal-return override is NOT credited as external yield
    (it is a capital event, not yield).  It still appears in inflow_ts.

    This is how tri-party loan principal corrections (e.g., the Anchorage
    S23 $5M Dec-19-2025 partial-principal return) avoid being over-counted
    as off-pool yield."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    # Two inflows from the same external source on different dates:
    #   - 2026-03-05: $891,780 (interest sweep — yield)
    #   - 2026-03-15: $5,000,000 (principal return — override-matched, not yield)
    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 5),   date(2026, 3, 15)],
        "counterparty": [cp_external,        cp_external],
        "signed_amount":[Decimal("891780"),  Decimal("5000000")],
    })
    overrides = {cp_external: [(date(2026, 3, 15), Decimal("5000000"))]}

    ts, ext_yield = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
        principal_return_overrides=overrides,
    )
    # Both rows in inflow_ts.
    assert ts["daily_inflow"].sum() == Decimal("5891780")
    # Only the interest sweep is external yield; principal return is excluded.
    assert ext_yield == Decimal("891780")


def test_cat_a_principal_return_override_within_dollar_tolerance(
    config_dir: Path,
):
    """Match tolerates ±$1 of rounding noise (per-token decimal scaling
    from on-chain data). $5,000,000.50 vs override $5,000,000 still
    matches (principal return, not yield)."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 15)],
        "counterparty": [cp_external],
        "signed_amount":[Decimal("5000000.50")],
    })
    overrides = {cp_external: [(date(2026, 3, 15), Decimal("5000000"))]}

    ts, ext_yield = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
        principal_return_overrides=overrides,
    )
    # Row is in inflow_ts as capital.
    assert ts["daily_inflow"].sum() == Decimal("5000000.50")
    # Override-matched → not external yield.
    assert ext_yield == Decimal("0")


def test_cat_a_principal_return_override_misses_on_amount_mismatch(
    config_dir: Path,
):
    """Override does NOT match if the amount is too different — the inflow
    is classified as external yield.  Guards against accidentally swallowing
    an unexpected inflow as capital."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 15)],
        "counterparty": [cp_external],
        "signed_amount":[Decimal("4500000")],   # $4.5M, not $5M
    })
    overrides = {cp_external: [(date(2026, 3, 15), Decimal("5000000"))]}

    ts, ext_yield = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
        principal_return_overrides=overrides,
    )
    # Row in inflow_ts.
    assert ts["daily_inflow"].sum() == Decimal("4500000")
    # No override match → classified as external yield.
    assert ext_yield == Decimal("4500000")


def test_cat_a_outflow_to_external_not_counted_as_yield(config_dir: Path):
    """A NEGATIVE signed_amount to an external-source address (e.g. a loan
    disbursement ALM → Anchorage) is included in inflow_ts as a capital
    outflow but is NOT credited as external yield (which is inbound only)."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 5)],
        "counterparty": [cp_external],
        "signed_amount":[Decimal("-150000000")],   # outflow from ALM
    })

    ts, ext_yield = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
    )
    # Outflow appears in inflow_ts as capital deployment.
    assert ts["daily_inflow"].sum() == Decimal("-150000000")
    # NOT credited as external yield (sign is negative).
    assert ext_yield == Decimal("0")


def test_cat_a_transit_pattern_nets_zero_in_inflow_ts(config_dir: Path):
    """When external yield arrives and is redeployed within the same period,
    the two legs cancel in inflow_ts → tw_avg stays near value_som (no
    spurious negative average-value)."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    cp_morpho   = _bytes20("0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")  # redeploy destination

    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 10),     date(2026, 3, 10)],
        "counterparty": [cp_external,           cp_morpho],
        "signed_amount":[Decimal("891780"),     Decimal("-891780")],
    })

    ts, ext_yield = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
    )
    # Net inflow is zero — arrival and redeployment cancel.
    assert ts["daily_inflow"].sum() == Decimal("0")
    # External yield is correctly captured.
    assert ext_yield == Decimal("891780")


def test_cat_a_empty_inflow_returns_empty(config_dir: Path):
    """No transfers in the period → empty timeseries, zero external yield."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date": [], "counterparty": [], "signed_amount": [],
    })
    ts, ext_yield = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources=set(),
    )
    assert ts.empty
    assert ext_yield == Decimal("0")


def test_load_prime_parses_principal_return_overrides_from_spark_yaml(
    config_dir: Path,
):
    """Spark's spark.yaml registers a principal-return override for the
    Anchorage escrow's 2025-12-19 $5M correction. Verify the loader
    surfaces it on the Prime value object so the compute layer can pass
    it into the Cat A classifier."""
    spark = load_prime(config_dir / "spark.yaml")
    eth_overrides = spark.principal_return_overrides[Chain.ETHEREUM]
    anchorage_escrow = next(
        a for a in spark.external_alm_sources[Chain.ETHEREUM]
        if str(a) == "0x49506c3aa028693458d6ee816b2ec28522946872"
    )
    entries = eth_overrides[anchorage_escrow]
    assert len(entries) >= 1
    dec_19 = next(e for e in entries if e.date == date(2025, 12, 19))
    assert dec_19.amount == Decimal("5000000")
    assert dec_19.token == "USDC"
