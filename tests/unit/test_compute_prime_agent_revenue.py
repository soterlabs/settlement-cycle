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


def _venue(vid: str = "V1", *, sky_direct: bool = False) -> Venue:
    return Venue(
        id=vid,
        chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, Address.from_str("0x" + "aa" * 20), "syrupUSDC", 6),
        pricing_category=PricingCategory.ERC4626_VAULT,
        underlying=Token(Chain.ETHEREUM, Address.from_str("0x" + "bb" * 20), "USDC", 6),
        label="Test Venue",
        sky_direct=sky_direct,
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


# --- Sky Direct Step 4 floor ------------------------------------------------

def test_sky_direct_outperformer_keeps_surplus():
    """ActualRev > BR_charge → prime keeps (ActualRev − BR_charge); shortfall = 0."""
    inputs = VenueRevenueInputs(
        venue=_venue("SD-out", sky_direct=True),
        value_som=Decimal("100_000_000"), value_eom=Decimal("110_000_000"),
        inflow_timeseries=_empty_inflow(),
        br_charge=Decimal("3_000_000"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.actual_revenue == Decimal("10_000_000")
    assert vr.br_charge == Decimal("3_000_000")
    assert vr.revenue == Decimal("7_000_000")
    assert vr.sky_direct_shortfall == Decimal("0")


def test_sky_direct_underperformer_floored_at_zero():
    """ActualRev < BR_charge → prime gets 0; Sky absorbs the shortfall."""
    inputs = VenueRevenueInputs(
        venue=_venue("SD-under", sky_direct=True),
        value_som=Decimal("100_000_000"), value_eom=Decimal("102_000_000"),
        inflow_timeseries=_empty_inflow(),
        br_charge=Decimal("3_500_000"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.actual_revenue == Decimal("2_000_000")
    assert vr.revenue == Decimal("0")
    assert vr.sky_direct_shortfall == Decimal("1_500_000")


def test_sky_direct_negative_actual_revenue_floored_at_zero():
    """Venue lost money (NAV drop) → prime still gets 0 (not negative); Sky
    absorbs the full shortfall = BR_charge − (negative_actual)."""
    inputs = VenueRevenueInputs(
        venue=_venue("SD-loss", sky_direct=True),
        value_som=Decimal("100_000_000"), value_eom=Decimal("99_000_000"),
        inflow_timeseries=_empty_inflow(),
        br_charge=Decimal("3_500_000"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.actual_revenue == Decimal("-1_000_000")
    assert vr.revenue == Decimal("0")
    assert vr.sky_direct_shortfall == Decimal("4_500_000")  # 3.5M - (-1M)


def test_non_sky_direct_venue_ignores_br_charge_field():
    """A non-Sky-Direct venue (default) keeps full revenue regardless of any
    accidentally-passed br_charge."""
    inputs = VenueRevenueInputs(
        venue=_venue("Normal", sky_direct=False),
        value_som=Decimal("100"), value_eom=Decimal("110"),
        inflow_timeseries=_empty_inflow(),
        br_charge=Decimal("5"),  # ignored — sky_direct=False
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.revenue == Decimal("10")
    assert vr.sky_direct_shortfall == Decimal("0")
    # actual_revenue still populated for downstream provenance / audit.
    assert vr.actual_revenue == Decimal("10")


def test_sky_direct_total_shortfall_aggregates_across_venues():
    """compute_prime_agent_revenue's total nets per-venue floors; orchestrator
    sums sky_direct_shortfall separately."""
    inputs = [
        VenueRevenueInputs(
            venue=_venue("SD1", sky_direct=True),
            value_som=Decimal("100"), value_eom=Decimal("105"),
            inflow_timeseries=_empty_inflow(),
            br_charge=Decimal("8"),  # underperforms by 3
        ),
        VenueRevenueInputs(
            venue=_venue("SD2", sky_direct=True),
            value_som=Decimal("200"), value_eom=Decimal("220"),
            inflow_timeseries=_empty_inflow(),
            br_charge=Decimal("10"),  # outperforms by 10
        ),
    ]
    total, breakdown = compute_prime_agent_revenue(_period(), inputs)
    # SD1 floored at 0, SD2 keeps 10 (= 20 − 10).
    assert total == Decimal("10")
    shortfall_total = sum((v.sky_direct_shortfall for v in breakdown), Decimal(0))
    assert shortfall_total == Decimal("3")
