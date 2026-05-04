"""Unit tests for `settle.compute.prime_agent_revenue`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from settle.compute.prime_agent_revenue import (
    VenueRevenueInputs,
    compute_prime_agent_revenue,
    compute_venue_revenue,
)
from settle.domain import Address, Chain, Period, PricingCategory, Token, Venue
from settle.domain.sde import SDEEntry


def _venue(vid: str = "V1") -> Venue:
    return Venue(
        id=vid,
        chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, Address.from_str("0x" + "aa" * 20), "syrupUSDC", 6),
        pricing_category=PricingCategory.ERC4626_VAULT,
        underlying=Token(Chain.ETHEREUM, Address.from_str("0x" + "bb" * 20), "USDC", 6),
        label="Test Venue",
    )


def _sde_fixed(venue_id: str = "SD") -> SDEEntry:
    return SDEEntry(
        prime_id="grove", venue_id=venue_id, chain="ethereum",
        kind="fixed", cap_usd=None, pattern=None,
        start_date=date(2025, 10, 30), end_date=None,
        label="test fixed", source="",
    )


def _sde_capped(venue_id: str, cap_usd: Decimal) -> SDEEntry:
    return SDEEntry(
        prime_id="grove", venue_id=venue_id, chain="ethereum",
        kind="capped", cap_usd=cap_usd, pattern=None,
        start_date=date(2025, 10, 23), end_date=None,
        label="test capped", source="",
    )


def _period() -> Period:
    return Period(
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
        pin_blocks={Chain.ETHEREUM: 24971074},
    )


def _empty_inflow() -> pd.DataFrame:
    return pd.DataFrame({"block_date": [], "daily_inflow": [], "cum_inflow": []})


# --- compute_venue_revenue --------------------------------------------------

def test_zero_change_zero_inflow_zero_revenue():
    inputs = VenueRevenueInputs(
        venue=_venue(), value_som=Decimal("100"), value_eom=Decimal("100"),
        inflow_timeseries=_empty_inflow(),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.revenue == Decimal("0")
    assert vr.period_inflow == Decimal("0")


def test_pure_mtm_growth_no_inflow():
    """OBEX-style: no new deposits during the period; revenue = MtM Δ."""
    inputs = VenueRevenueInputs(
        venue=_venue(), value_som=Decimal("600_000_000"), value_eom=Decimal("610_000_000"),
        inflow_timeseries=_empty_inflow(),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.revenue == Decimal("10_000_000")
    assert vr.value_som == Decimal("600_000_000")
    assert vr.value_eom == Decimal("610_000_000")
    assert vr.period_inflow == Decimal("0")


def test_inflow_during_period_subtracts_from_mtm_delta():
    """20M deposited on Mar 5, 30M MtM growth → revenue = 30M - 20M = 10M."""
    inflow_df = pd.DataFrame({
        "block_date":   [date(2026, 3, 5)],
        "daily_inflow": [20_000_000.0],
        "cum_inflow":   [20_000_000.0],
    })
    inputs = VenueRevenueInputs(
        venue=_venue(), value_som=Decimal("100_000_000"), value_eom=Decimal("130_000_000"),
        inflow_timeseries=inflow_df,
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.period_inflow == Decimal("20000000.0")
    assert vr.revenue == Decimal("10_000_000")


def test_inflow_before_period_does_not_count():
    """Inflows before period.start contribute to value_som, not period_inflow."""
    inflow_df = pd.DataFrame({
        "block_date":   [date(2025, 11, 18), date(2025, 12, 1)],
        "daily_inflow": [50_000_000.0,        20_000_000.0],
        "cum_inflow":   [50_000_000.0,        70_000_000.0],
    })
    inputs = VenueRevenueInputs(
        venue=_venue(), value_som=Decimal("70_000_000"), value_eom=Decimal("75_000_000"),
        inflow_timeseries=inflow_df,
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.period_inflow == Decimal("0")     # no deposits Mar 1-31
    assert vr.revenue == Decimal("5_000_000")


def test_inflow_straddling_period_counts_only_within():
    """Inflows on Feb 28 and Mar 5: only the Mar 5 amount counts as period_inflow."""
    inflow_df = pd.DataFrame({
        "block_date":   [date(2026, 2, 28), date(2026, 3, 5)],
        "daily_inflow": [10_000_000.0,       3_000_000.0],
        "cum_inflow":   [10_000_000.0,       13_000_000.0],
    })
    inputs = VenueRevenueInputs(
        venue=_venue(), value_som=Decimal("0"), value_eom=Decimal("0"),
        inflow_timeseries=inflow_df,
    )
    vr = compute_venue_revenue(_period(), inputs)
    # cum at Feb 28 = 10M (= som_anchor since period.start − 1 = Feb 28)
    # cum at Mar 31 = 13M
    # period_inflow = 3M
    assert vr.period_inflow == Decimal("3000000.0")
    assert vr.revenue == Decimal("-3000000.0")  # all inflow, no MtM growth


def test_negative_revenue_when_inflow_exceeds_mtm():
    """Edge case: deposit happens, MtM dips slightly. Revenue is negative."""
    inflow_df = pd.DataFrame({
        "block_date":   [date(2026, 3, 10)],
        "daily_inflow": [100.0],
        "cum_inflow":   [100.0],
    })
    inputs = VenueRevenueInputs(
        venue=_venue(), value_som=Decimal("1000"), value_eom=Decimal("1099"),
        inflow_timeseries=inflow_df,
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.revenue == Decimal("-1")


# --- compute_prime_agent_revenue -------------------------------------------

def test_prime_revenue_sums_per_venue():
    inputs = [
        VenueRevenueInputs(
            venue=_venue("V1"),
            value_som=Decimal("100"), value_eom=Decimal("110"),
            inflow_timeseries=_empty_inflow(),
        ),
        VenueRevenueInputs(
            venue=_venue("V2"),
            value_som=Decimal("200"), value_eom=Decimal("215"),
            inflow_timeseries=_empty_inflow(),
        ),
    ]
    total, breakdown = compute_prime_agent_revenue(_period(), inputs)
    assert total == Decimal("25")
    assert len(breakdown) == 2
    assert breakdown[0].venue_id == "V1" and breakdown[0].revenue == Decimal("10")
    assert breakdown[1].venue_id == "V2" and breakdown[1].revenue == Decimal("15")


def test_prime_revenue_empty_venue_list_yields_zero():
    total, breakdown = compute_prime_agent_revenue(_period(), [])
    assert total == Decimal("0")
    assert breakdown == []


# --- SDE split (kind=fixed) -------------------------------------------------

def test_sde_fixed_all_revenue_to_sky():
    """kind=fixed → sd_share=1; prime keeps 0, Sky takes full actual_revenue."""
    inputs = VenueRevenueInputs(
        venue=_venue("SD-out"),
        value_som=Decimal("100_000_000"), value_eom=Decimal("110_000_000"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_fixed("SD-out"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.actual_revenue == Decimal("10_000_000")
    assert vr.sd_share == Decimal("1")
    assert vr.sd_revenue == Decimal("10_000_000")
    assert vr.revenue == Decimal("0")


def test_sde_fixed_negative_actual_revenue_absorbed_by_sky():
    """Loss on a fixed-SDE venue: Sky absorbs the full negative number."""
    inputs = VenueRevenueInputs(
        venue=_venue("SD-loss"),
        value_som=Decimal("100_000_000"), value_eom=Decimal("99_000_000"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_fixed("SD-loss"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.actual_revenue == Decimal("-1_000_000")
    assert vr.sd_revenue == Decimal("-1_000_000")
    assert vr.revenue == Decimal("0")


def test_non_sde_venue_keeps_full_revenue():
    """No SDE entry → sd_share=0, prime keeps full actual_revenue."""
    inputs = VenueRevenueInputs(
        venue=_venue("Normal"),
        value_som=Decimal("100"), value_eom=Decimal("110"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=None,
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.revenue == Decimal("10")
    assert vr.sd_share == Decimal("0")
    assert vr.sd_revenue == Decimal("0")
    assert vr.actual_revenue == Decimal("10")


# --- SDE split (kind=capped) ------------------------------------------------

def test_sde_capped_splits_revenue_proportionally():
    """JAAA-style: cap=$325M on a $454M position → sd_share = 325/454.
    Revenue split applies that ratio."""
    inputs = VenueRevenueInputs(
        venue=_venue("JAAA"),
        value_som=Decimal("454_000_000"), value_eom=Decimal("455_388_581"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("JAAA", Decimal("325_000_000")),
    )
    vr = compute_venue_revenue(_period(), inputs)
    expected_share = Decimal("325_000_000") / Decimal("454_000_000")
    assert vr.actual_revenue == Decimal("1_388_581")
    assert vr.sd_share == expected_share
    assert vr.sd_revenue == Decimal("1_388_581") * expected_share
    assert vr.revenue == Decimal("1_388_581") * (Decimal("1") - expected_share)


def test_sde_capped_when_value_below_cap_is_fully_sd():
    """Position below cap → sd_share = 1 (everything is Sky's)."""
    inputs = VenueRevenueInputs(
        venue=_venue("JAAA-small"),
        value_som=Decimal("100_000_000"), value_eom=Decimal("101_000_000"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("JAAA-small", Decimal("325_000_000")),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.sd_share == Decimal("1")
    assert vr.sd_revenue == Decimal("1_000_000")
    assert vr.revenue == Decimal("0")


def test_compute_prime_revenue_sums_only_prime_share():
    """Total prime_agent_revenue = Σ revenue (already net of SDE split)."""
    inputs = [
        VenueRevenueInputs(
            venue=_venue("V1"),
            value_som=Decimal("100"), value_eom=Decimal("110"),
            inflow_timeseries=_empty_inflow(),
        ),
        VenueRevenueInputs(
            venue=_venue("SD1"),
            value_som=Decimal("100"), value_eom=Decimal("105"),
            inflow_timeseries=_empty_inflow(),
            sde_entry=_sde_fixed("SD1"),
        ),
    ]
    total, breakdown = compute_prime_agent_revenue(_period(), inputs)
    assert total == Decimal("10")  # V1's $10; SD1 contributes 0 to prime
    assert sum((v.sd_revenue for v in breakdown), Decimal(0)) == Decimal("5")
