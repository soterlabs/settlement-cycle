"""Unit tests for ``_RoutedBalances`` routing in ``grove_fixture_loader``.

Exercises the three cases that matter:

* ``min_transfer_amount=0`` with a ``cum_balance_*_raw`` fixture present →
  returns the raw (unfiltered) series. This is the SDE-asset-value path.
* ``min_transfer_amount=0`` with NO ``_raw`` fixture present → falls back
  to the default filtered fixture. JTRSY's default capture is already
  unfiltered (no ``min_transfer_amount_usd`` on E9), so this case is
  benign in practice.
* ``min_transfer_amount=None`` (or non-zero) → always returns the
  default filtered fixture. This is the Cat E inflow path.

Also verifies routing keys by ``(token, holder)`` so an SDE venue with
``holder_override`` doesn't fall through to the filtered fixture.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from replay.grove_fixture_loader import (
    build_grove_sources,
    load_grove_and_fixtures,
)


_REPO = Path(__file__).resolve().parents[2]


def _grove_with_buidl_raw():
    """Q1 fixture has both ``cum_balance_e10`` (filtered, min_transfer=$1M)
    and ``cum_balance_e10_raw`` (unfiltered, min_transfer=0). Use it for
    the routing tests."""
    grove, fixtures, blocks = load_grove_and_fixtures(_REPO, "grove_2026_03")
    return grove, build_grove_sources(grove, fixtures, blocks)


def _buidl_addr(grove):
    """E10 BUIDL token address (bytes)."""
    e10 = next(v for v in grove.venues if v.id == "E10")
    return e10.token.address.value


def _jtrsy_addr(grove):
    """E9 JTRSY token address (bytes). Default fixture is captured with
    ``min_transfer=0`` per E9's config (no ``min_transfer_amount_usd``),
    so there is NO ``_raw`` fixture for E9 — the default already is raw."""
    e9 = next(v for v in grove.venues if v.id == "E9")
    return e9.token.address.value


def _alm_eth(grove):
    """Grove's Ethereum ALM address (bytes) — the standard SDE holder."""
    from settle.domain import Chain
    return grove.alm[Chain.ETHEREUM].value


def test_min_transfer_zero_with_raw_fixture_returns_raw_series():
    """BUIDL: passing ``min_transfer_amount=0`` should return the raw
    (unfiltered) series captured under ``cum_balance_e10_raw``. The raw
    series includes sub-$1M BlackRock yield-distribution mints; the
    default filtered series strips them. The two series differ by a
    monotonically-growing offset over time (the cumulative yield-mint
    dust). Mar 31 specifically: raw ≈ $706.7M, filtered ≈ $698.3M
    (off by ~$8.5M of yield-mint dust)."""
    grove, sources = _grove_with_buidl_raw()
    df = sources.balance.cumulative_balance_timeseries(
        chain="ethereum",
        token=_buidl_addr(grove),
        holder=_alm_eth(grove),
        start=grove.start_date,
        pin_block=24781026,    # Mar 31 EOM
        min_transfer_amount=Decimal(0),
    )
    assert not df.empty
    # Find Mar 31 row and confirm we got the raw value (matches balanceOf).
    mar_31 = df[df["block_date"] == date(2026, 3, 31)]
    assert not mar_31.empty
    bal = Decimal(str(mar_31.iloc[0]["cum_balance"]))
    # Raw cum_balance Mar 31 = $706,747,881.32 (matches on-chain balanceOf).
    assert Decimal("706_000_000") < bal < Decimal("707_000_000"), (
        f"Expected raw BUIDL Mar 31 ≈ $706.7M; got ${bal:,}"
    )


def test_min_transfer_one_million_returns_filtered_series():
    """BUIDL: passing ``min_transfer_amount=$1M`` (the venue's default)
    should return the FILTERED series — yield-mint dust stripped, used
    for capital-inflow cost-basis tracking. Mar 31 filtered ≈ $698.3M."""
    grove, sources = _grove_with_buidl_raw()
    df = sources.balance.cumulative_balance_timeseries(
        chain="ethereum",
        token=_buidl_addr(grove),
        holder=_alm_eth(grove),
        start=grove.start_date,
        pin_block=24781026,
        min_transfer_amount=Decimal("1000000"),
    )
    mar_31 = df[df["block_date"] == date(2026, 3, 31)]
    assert not mar_31.empty
    bal = Decimal(str(mar_31.iloc[0]["cum_balance"]))
    # Filtered cum_balance Mar 31 = $698,277,167.
    assert Decimal("697_000_000") < bal < Decimal("699_000_000"), (
        f"Expected filtered BUIDL Mar 31 ≈ $698.3M; got ${bal:,}"
    )


def test_min_transfer_zero_falls_back_to_default_when_no_raw_fixture():
    """JTRSY: no ``cum_balance_e9_raw`` fixture exists (E9's default is
    already captured raw because the venue has no ``min_transfer_amount_usd``).
    Passing ``min_transfer=0`` should fall back to the default
    ``cum_balance_e9`` fixture."""
    grove, sources = _grove_with_buidl_raw()
    df_zero = sources.balance.cumulative_balance_timeseries(
        chain="ethereum",
        token=_jtrsy_addr(grove),
        holder=_alm_eth(grove),
        start=grove.start_date,
        pin_block=24781026,
        min_transfer_amount=Decimal(0),
    )
    df_none = sources.balance.cumulative_balance_timeseries(
        chain="ethereum",
        token=_jtrsy_addr(grove),
        holder=_alm_eth(grove),
        start=grove.start_date,
        pin_block=24781026,
        min_transfer_amount=None,
    )
    # When no raw fixture exists, both paths return the same default series.
    assert df_zero.equals(df_none), (
        "JTRSY has no _raw fixture, so min_transfer=0 should fall back to "
        "the default series identical to min_transfer=None."
    )


def test_min_transfer_none_always_returns_default_filtered_series():
    """BUIDL: passing ``min_transfer_amount=None`` (the default arg) should
    return the FILTERED series, NOT the raw series. Critical: the Cat E
    inflow path doesn't pass min_transfer_amount; it must NOT accidentally
    pick up the raw fixture even though it's captured."""
    grove, sources = _grove_with_buidl_raw()
    df = sources.balance.cumulative_balance_timeseries(
        chain="ethereum",
        token=_buidl_addr(grove),
        holder=_alm_eth(grove),
        start=grove.start_date,
        pin_block=24781026,
        # min_transfer_amount=None  — explicit default
    )
    mar_31 = df[df["block_date"] == date(2026, 3, 31)]
    bal = Decimal(str(mar_31.iloc[0]["cum_balance"]))
    # Must be the filtered value, not the raw $706.7M.
    assert Decimal("697_000_000") < bal < Decimal("699_000_000"), (
        f"min_transfer=None must return FILTERED BUIDL series (~$698.3M); "
        f"got ${bal:,} — the routing accidentally returned the raw series."
    )


def test_routing_keys_by_token_and_holder():
    """A token+holder combination not captured in either fixture dict
    must fall through to the empty frame, not return a mismatched series."""
    grove, sources = _grove_with_buidl_raw()
    # Look up BUIDL token but with a wrong holder (32 random bytes truncated).
    wrong_holder = bytes(20)
    df = sources.balance.cumulative_balance_timeseries(
        chain="ethereum",
        token=_buidl_addr(grove),
        holder=wrong_holder,
        start=grove.start_date,
        pin_block=24781026,
        min_transfer_amount=Decimal(0),
    )
    assert df.empty, (
        "An unknown (token, holder) pair must return an empty frame, "
        "not accidentally serve another venue's series."
    )
