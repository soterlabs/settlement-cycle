"""Unit tests for `settle.compute.prime_agent_revenue`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from settle.compute.prime_agent_revenue import (
    VenueRevenueInputs,
    _time_weighted_avg_value,
    _time_weighted_notional,
    compute_prime_agent_revenue,
    compute_venue_revenue,
)
from settle.domain import (
    Address,
    Chain,
    NotionalScheduleEntry,
    Period,
    PricingCategory,
    Token,
    Venue,
)
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
    # cum at Feb 28 = 10M (= som_anchor since period.start - 1 = Feb 28)
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
    """JAAA-style: cap=$325M on a ~$455M EoM position → EoM-locked
    sd_share = 325 / value_eom (matches Grove team's PnL workbook
    methodology; see ``_capped_sd_revenue_eom_locked``)."""
    eom = Decimal("455_388_581")
    cap = Decimal("325_000_000")
    inputs = VenueRevenueInputs(
        venue=_venue("JAAA"),
        value_som=Decimal("454_000_000"), value_eom=eom,
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("JAAA", cap),
    )
    vr = compute_venue_revenue(_period(), inputs)
    expected_share = cap / eom
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


# --- SDE split (capped, daily-resolved + burn-day override) ----------------
#
# `_capped_sd_revenue_daily_resolved` is the post-2026-06 methodology that
# replaces the EoM-locked snapshot when a daily value_timeseries is
# available. Mirrors Grove's per-day allocation logic in their
# `<Asset>_ETH Allocation` sheets — sd_share = Σ_d cum_value / Σ_d uncapped.
#
# The burn-day override short-circuits to sd_share = 1.0 when the SDE
# entry's burn_date falls inside the period AND value_eom < cap_usd,
# matching Grove's burn-month behaviour (JAAA Mar 2026: ~98% of net P&L
# to Sky). Without the override, the daily-Σ method under-attributes
# because `cum_value` drops to 0 from usdc_settlement_date onward.

def _sde_capped_with_burn(
    venue_id: str, cap_usd: Decimal, burn: date, settle: date, end: date,
) -> SDEEntry:
    return SDEEntry(
        prime_id="grove", venue_id=venue_id, chain="ethereum",
        kind="capped", cap_usd=cap_usd, pattern=None,
        start_date=date(2025, 10, 23), end_date=end,
        burn_date=burn, usdc_settlement_date=settle,
        label="test capped w/ burn", source="",
    )


def _daily_value_ts(rows: list[tuple[date, Decimal, Decimal]]) -> pd.DataFrame:
    """Build a `value_timeseries` from (block_date, cum_value, uncapped_value)
    tuples. Mirrors the schema produced by `_sde_asset_value_timeseries`."""
    return pd.DataFrame([
        {"block_date": d, "cum_value": c, "uncapped_value": u}
        for d, c, u in rows
    ])


def test_daily_resolved_matches_eom_when_position_is_stable():
    """For a constant-value, constant-share month the daily-Σ method must
    coincide with the EoM-locked snapshot. Feb 2026 JAAA ($454M throughout,
    no inflows) is the canonical example: both methods give
    actual_revenue × cap / value_eom."""
    cap = Decimal("325_000_000")
    val = Decimal("454_000_000")
    period = Period(start=date(2026, 2, 1), end=date(2026, 2, 28),
                    pin_blocks={Chain.ETHEREUM: 24558867})
    # Stable position: every day cum_value=cap, uncapped_value=val.
    ts = _daily_value_ts([
        (date(2026, 2, d), cap, val) for d in range(1, 29)
    ])
    inputs = VenueRevenueInputs(
        venue=_venue("JAAA"),
        value_som=val, value_eom=val,
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("JAAA", cap),
        value_timeseries=ts,
    )
    vr = compute_venue_revenue(period, inputs)
    assert vr.actual_revenue == Decimal("0")
    # Σ cum / Σ uncapped = 28×cap / 28×val = cap / val — same as EoM ratio.
    expected_share = cap / val
    assert vr.sd_share == expected_share
    assert vr.sd_revenue == Decimal("0")  # 0 × share = 0


def test_daily_resolved_with_mid_period_redemption_matches_grove():
    """Jan 2026 JAAA: $751M → $454M mid-month redemption. The daily method
    must produce a share between the SoM (43.2%) and EoM (71.5%) shares,
    weighted by daily value. With Grove's actual daily series the
    effective share is 60.64% — but this test uses a simplified two-segment
    series to verify the value-weighted average."""
    cap = Decimal("325_000_000")
    # Days 1-5 at $751M (cap binds at 43.22%); days 6-31 at $454M (71.55%).
    period = Period(start=date(2026, 1, 1), end=date(2026, 1, 31),
                    pin_blocks={Chain.ETHEREUM: 24358292})
    rows = []
    for d in range(1, 6):
        rows.append((date(2026, 1, d), cap, Decimal("751_935_242")))
    for d in range(6, 32):
        rows.append((date(2026, 1, d), cap, Decimal("454_188_057")))
    ts = _daily_value_ts(rows)
    actual_revenue = Decimal("2_363_169")
    inputs = VenueRevenueInputs(
        venue=_venue("JAAA"),
        value_som=Decimal("751_935_538"), value_eom=Decimal("454_188_405"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("JAAA", cap),
        value_timeseries=ts,
    )
    # Force the (value_eom − value_som) − period_inflow path to match actual_revenue
    # by using override.
    inputs2 = VenueRevenueInputs(
        venue=inputs.venue, value_som=inputs.value_som, value_eom=inputs.value_eom,
        inflow_timeseries=inputs.inflow_timeseries, sde_entry=inputs.sde_entry,
        actual_revenue_override=actual_revenue, value_timeseries=ts,
    )
    vr = compute_venue_revenue(period, inputs2)
    # Σ cum = 31 × cap = 31 × 325M
    # Σ uncapped = 5 × 751.9M + 26 × 454.2M = 3,759.7M + 11,808.9M = 15,568.6M
    expected_share = (Decimal(31) * cap) / (Decimal(5) * Decimal("751_935_242") + Decimal(26) * Decimal("454_188_057"))
    assert abs(vr.sd_share - expected_share) < Decimal("1e-10")
    # The share should be between SoM (cap/751M ≈ 43.2%) and EoM (cap/454M ≈ 71.5%)
    assert Decimal("0.43") < vr.sd_share < Decimal("0.72")
    assert vr.sd_revenue == actual_revenue * vr.sd_share


def test_daily_resolved_burn_day_override_fires_when_burn_in_period_and_value_eom_below_cap():
    """Mar 2026 JAAA: burn on Mar 9, USDC settled Mar 11, end_date Mar 12.
    Post-burn value_eom ($128M) < cap ($325M) → override fires →
    sd_share = 1.0 → Sky absorbs the full period's actual_revenue.
    Matches Grove's Mar 2026 workbook attribution to JAAA_ETH_Sky."""
    cap = Decimal("325_000_000")
    period = Period(start=date(2026, 3, 1), end=date(2026, 3, 31),
                    pin_blocks={Chain.ETHEREUM: 24971074})
    actual_revenue = Decimal("-477_414")
    # Build a series with non-trivial cum/uncapped sums — the override should
    # ignore them and return sd_share = 1.0 regardless.
    rows = [(date(2026, 3, d), cap, Decimal("455_000_000")) for d in range(1, 9)]
    rows += [(date(2026, 3, d), cap, Decimal("128_000_000")) for d in range(9, 12)]
    rows += [(date(2026, 3, d), Decimal("0"), Decimal("128_000_000")) for d in range(12, 32)]
    ts = _daily_value_ts(rows)
    inputs = VenueRevenueInputs(
        venue=_venue("JAAA"),
        value_som=Decimal("455_576_922"), value_eom=Decimal("128_240_934"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped_with_burn(
            "JAAA", cap, date(2026, 3, 9), date(2026, 3, 11), date(2026, 3, 12),
        ),
        actual_revenue_override=actual_revenue,
        value_timeseries=ts,
    )
    vr = compute_venue_revenue(period, inputs)
    assert vr.sd_share == Decimal("1")
    assert vr.sd_revenue == actual_revenue
    assert vr.revenue == Decimal("0")  # nothing to prime — Sky takes full loss


def test_daily_resolved_burn_day_override_does_not_fire_when_value_eom_above_cap():
    """Defensive: if the burn happens but value_eom is still above the cap
    (the position hasn't actually shrunk past the cap-protected slice),
    the override must NOT fire. Daily-Σ runs normally instead."""
    cap = Decimal("325_000_000")
    period = Period(start=date(2026, 3, 1), end=date(2026, 3, 31),
                    pin_blocks={Chain.ETHEREUM: 24971074})
    rows = [(date(2026, 3, d), cap, Decimal("455_000_000")) for d in range(1, 32)]
    ts = _daily_value_ts(rows)
    inputs = VenueRevenueInputs(
        venue=_venue("JAAA"),
        value_som=Decimal("455_000_000"), value_eom=Decimal("455_000_000"),  # > cap
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped_with_burn(
            "JAAA", cap, date(2026, 3, 9), date(2026, 3, 11), date(2026, 3, 12),
        ),
        actual_revenue_override=Decimal("100_000"),
        value_timeseries=ts,
    )
    vr = compute_venue_revenue(period, inputs)
    expected_share = cap / Decimal("455_000_000")  # daily-Σ value-weighted = constant ratio
    assert abs(vr.sd_share - expected_share) < Decimal("1e-10")
    assert vr.sd_share < Decimal("1")


def test_daily_resolved_burn_day_override_does_not_fire_when_burn_outside_period():
    """Apr 2026 JAAA: SDE deactivated 2026-03-12 (end_date), burn was Mar 9.
    For April, burn_date is NOT in [period.start, period.end] → override
    must not fire. In practice the SDE entry's end_date check upstream
    prevents the entry from being applied to April at all, but this test
    verifies the override-only guard in isolation."""
    cap = Decimal("325_000_000")
    period = Period(start=date(2026, 4, 1), end=date(2026, 4, 30),
                    pin_blocks={Chain.ETHEREUM: 24971074})
    # value below cap, but burn was last month — override must NOT fire
    rows = [(date(2026, 4, d), Decimal("0"), Decimal("128_000_000")) for d in range(1, 31)]
    ts = _daily_value_ts(rows)
    inputs = VenueRevenueInputs(
        venue=_venue("JAAA"),
        value_som=Decimal("128_240_934"), value_eom=Decimal("128_831_404"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped_with_burn(
            "JAAA", cap, date(2026, 3, 9), date(2026, 3, 11), date(2026, 3, 12),
        ),
        actual_revenue_override=Decimal("590_470"),
        value_timeseries=ts,
    )
    vr = compute_venue_revenue(period, inputs)
    # cum_value = 0 every day → sd_share = 0 → all revenue to Prime
    assert vr.sd_share == Decimal("0")
    assert vr.sd_revenue == Decimal("0")
    assert vr.revenue == Decimal("590_470")


def test_daily_resolved_empty_timeseries_falls_back_to_eom_locked():
    """If `value_timeseries` is None (legacy path: tests constructing
    VenueRevenueInputs directly without the SDE timeseries), the EoM-locked
    fallback runs. This preserves backward compatibility with any caller
    that hasn't been migrated to plumb the timeseries through."""
    cap = Decimal("325_000_000")
    eom = Decimal("454_000_000")
    inputs = VenueRevenueInputs(
        venue=_venue("JAAA"),
        value_som=Decimal("750_000_000"), value_eom=eom,
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("JAAA", cap),
        actual_revenue_override=Decimal("2_363_115"),
        # value_timeseries=None — the fallback condition
    )
    vr = compute_venue_revenue(_period(), inputs)
    expected_share = cap / eom
    assert vr.sd_share == expected_share
    assert vr.sd_revenue == Decimal("2_363_115") * expected_share


def test_daily_resolved_with_erc4626_period_inflow_burn_override_end_to_end():
    """Production path coverage: JAAA Mar 2026 uses ``erc4626_period_inflow``
    (Centrifuge Deposit/Withdraw event amounts) to compute actual_revenue,
    AND the burn-day override fires because value_eom < cap. This test
    exercises the full Cat E composition without the test-only
    ``actual_revenue_override`` shortcut — the production code path on
    burn months goes through erc4626_period_inflow."""
    cap = Decimal("325_000_000")
    period = Period(start=date(2026, 3, 1), end=date(2026, 3, 31),
                    pin_blocks={Chain.ETHEREUM: 24971074})
    # JAAA Mar 2026 actuals (rounded):
    #   value_som = $455.58M, value_eom = $128.24M (post-burn residual),
    #   period_inflow = -$326.86M (Sky redemption out via Centrifuge),
    #   actual_revenue = (128.24 − 455.58) − (−326.86) = -$0.48M ≈ -$477K.
    value_som = Decimal("455_576_922")
    value_eom = Decimal("128_240_934")
    inflow = Decimal("-326_858_574")
    expected_actual_rev = (value_eom - value_som) - inflow  # = -$477,414
    # Plausible daily timeseries — the override should ignore Σ details and
    # return sd_share = 1.0.
    rows = [(date(2026, 3, d), cap, value_som) for d in range(1, 9)]   # pre-burn
    rows += [(date(2026, 3, d), cap, value_eom) for d in range(9, 12)] # in-flight
    rows += [(date(2026, 3, d), Decimal("0"), value_eom) for d in range(12, 32)]
    ts = _daily_value_ts(rows)
    inputs = VenueRevenueInputs(
        venue=_venue("JAAA"),
        value_som=value_som, value_eom=value_eom,
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped_with_burn(
            "JAAA", cap, date(2026, 3, 9), date(2026, 3, 11), date(2026, 3, 12),
        ),
        erc4626_period_inflow=inflow,   # production Cat E inflow path
        value_timeseries=ts,
    )
    vr = compute_venue_revenue(period, inputs)
    # actual_revenue must come from the erc4626 branch, NOT a hardcoded override.
    assert vr.actual_revenue == expected_actual_rev
    # Burn-day override fires (burn ∈ period, value_eom < cap) → sd_share = 1.
    assert vr.sd_share == Decimal("1")
    assert vr.sd_revenue == expected_actual_rev
    assert vr.period_inflow == inflow
    assert vr.revenue == Decimal("0")    # nothing to Prime


def test_curve_lp_daily_resolved_timeseries_has_required_columns():
    """Regression: `_curve_sde_asset_value_timeseries` (Curve LP SDE path)
    must emit BOTH `cum_value` AND `uncapped_value` columns so it's
    interchangeable with `_sde_asset_value_timeseries` when fed into
    `_capped_sd_revenue_daily_resolved`. A missing `uncapped_value` would
    raise KeyError when iterating rows — found in code review."""
    # Build a minimal Curve-shape timeseries (same schema as RWA SDE) and
    # verify the daily-resolved function can consume it without KeyError.
    cap = Decimal("100_000_000")
    period = Period(start=date(2026, 2, 1), end=date(2026, 2, 3),
                    pin_blocks={Chain.ETHEREUM: 24558867})
    # 3 days, position at cap throughout → sd_share = 1.0 with no burn.
    ts = _daily_value_ts([
        (date(2026, 2, 1), cap, Decimal("100_000_000")),
        (date(2026, 2, 2), cap, Decimal("100_000_000")),
        (date(2026, 2, 3), cap, Decimal("100_000_000")),
    ])
    inputs = VenueRevenueInputs(
        venue=_venue("S24"),
        value_som=Decimal("100_000_000"), value_eom=Decimal("100_000_000"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("S24", cap),
        actual_revenue_override=Decimal("500_000"),
        value_timeseries=ts,
    )
    vr = compute_venue_revenue(period, inputs)
    assert vr.sd_share == Decimal("1")
    assert vr.sd_revenue == Decimal("500_000")


# --- external_revenue ------------------------------------------------------
#
# Cat C aToken Merkl-style drops arrive as a separate revenue stream that
# the closed-form ``yield = scaled(SoM) x dindex / RAY`` formula doesn't
# capture. The orchestrator computes it via ``_atoken_external_revenue_usd``
# and threads it through ``VenueRevenueInputs.external_revenue``. These
# tests pin the propagation + SDE-interaction semantics.

def test_external_revenue_defaults_to_zero():
    """No external_alm_sources configured → field stays 0 and revenue is
    unchanged vs the pre-Option-A behaviour."""
    inputs = VenueRevenueInputs(
        venue=_venue(),
        value_som=Decimal("100_000_000"), value_eom=Decimal("110_000_000"),
        inflow_timeseries=_empty_inflow(),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.external_revenue == Decimal("0")
    assert vr.revenue == Decimal("10_000_000")


def test_external_revenue_adds_to_prime_revenue_non_sde():
    """For a non-SDE venue: revenue = actual_revenue + external_revenue.
    Mirrors Grove E1 / E3 receiving Merkl drops outside the pool-native yield."""
    inputs = VenueRevenueInputs(
        venue=_venue(),
        value_som=Decimal("100_000_000"), value_eom=Decimal("101_000_000"),
        inflow_timeseries=_empty_inflow(),
        external_revenue=Decimal("821_306"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.actual_revenue == Decimal("1_000_000")      # closed-form yield
    assert vr.external_revenue == Decimal("821_306")      # Merkl-style drop
    assert vr.revenue == Decimal("1_821_306")             # both to prime


def test_external_revenue_bypasses_sde_split():
    """SDE venues split ``actual_revenue`` between Sky and prime, but the
    ``external_revenue`` stream goes 100% to prime — off-pool rewards aren't
    part of the SDE deal terms."""
    inputs = VenueRevenueInputs(
        venue=_venue("SD"),
        value_som=Decimal("100"), value_eom=Decimal("200"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_fixed("SD"),
        external_revenue=Decimal("50"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    # SDE fixed → sd_share = 1 → all actual_revenue (100) goes to Sky
    assert vr.actual_revenue == Decimal("100")
    assert vr.sd_share == Decimal("1")
    assert vr.sd_revenue == Decimal("100")
    # Prime still gets the full external_revenue
    assert vr.external_revenue == Decimal("50")
    assert vr.revenue == Decimal("50")


def test_external_revenue_with_capped_sde():
    """Capped SDE under EoM-locked: sd_share = min(cap, value_eom) / value_eom
    applies to actual_revenue; external_revenue still 100% to prime."""
    inputs = VenueRevenueInputs(
        venue=_venue("SD"),
        value_som=Decimal("100"), value_eom=Decimal("200"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("SD", Decimal("50")),  # EoM-locked sd_share = 50/200 = 0.25
        external_revenue=Decimal("30"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    # actual_revenue = 100, sd_share = 50/200 = 0.25
    #   → Sky gets 25, prime gets 75 + 30 external = 105
    assert vr.actual_revenue == Decimal("100")
    assert vr.sd_share == Decimal("0.25")
    assert vr.sd_revenue == Decimal("25")
    assert vr.external_revenue == Decimal("30")
    assert vr.revenue == Decimal("105")


def test_external_revenue_rolls_up_to_prime_total():
    """Sanity: across multiple venues, prime_agent_revenue == sum of per-venue
    ``revenue`` fields (which already include external_revenue)."""
    inputs = [
        VenueRevenueInputs(
            venue=_venue("V1"),
            value_som=Decimal("100"), value_eom=Decimal("110"),
            inflow_timeseries=_empty_inflow(),
            external_revenue=Decimal("5"),
        ),
        VenueRevenueInputs(
            venue=_venue("V2"),
            value_som=Decimal("100"), value_eom=Decimal("100"),
            inflow_timeseries=_empty_inflow(),
            external_revenue=Decimal("3"),
        ),
    ]
    total, breakdown = compute_prime_agent_revenue(_period(), inputs)
    # V1: actual_revenue 10 + external 5 = 15
    # V2: actual_revenue 0  + external 3 =  3
    assert total == Decimal("18")
    assert {vr.venue_id: vr.external_revenue for vr in breakdown} == {
        "V1": Decimal("5"), "V2": Decimal("3"),
    }


# --- _time_weighted_avg_value -----------------------------------------------
#
# The simple (value_som + value_eom)/2 average is wrong when inflows are
# concentrated in time. These tests pin the time-weighted helper so a
# regression to SoM/EoM averaging would fail loudly. The "day-28 spike"
# scenario is what motivated the helper (real Maple/sUSDS-at-ALM flows
# pattern).

def test_tw_avg_flat_venue_returns_value_som():
    """No inflows, stable position → tw_avg ≡ value_som."""
    out = _time_weighted_avg_value(
        _period(), Decimal("100000000"), _empty_inflow(),
    )
    assert out == Decimal("100000000")


def test_tw_avg_day_28_spike_is_not_som_eom_avg():
    """$300M deposited on day 28 of a 31-day month.

    SoM/EoM avg = $150M. True time-weighted avg = 300M × 4 / 31 ≈ $38.7M.
    The helper must return ~$38.7M, not $150M — the latter would inflate
    this venue's CoF allocation by ~3.9× in the reporting sheet.
    """
    # Single inflow row on 2026-03-28; cum_inflow stays at 300M after.
    inflow = pd.DataFrame({
        "block_date":   [date(2026, 3, 28)],
        "daily_inflow": [Decimal("300000000")],
        "cum_inflow":   [Decimal("300000000")],
    })
    out = _time_weighted_avg_value(
        _period(), Decimal("0"), inflow,
    )
    # Days 1–27 → $0; days 28–31 → $300M. Mean = 300M × 4 / 31.
    expected = Decimal("300000000") * Decimal("4") / Decimal("31")
    assert out == expected
    # And not even close to SoM/EoM avg ($150M).
    assert out < Decimal("50000000")


def test_tw_avg_day_3_deposit_matches_long_duration():
    """Mirror case: $300M deposited on day 3 → ~$280M tw_avg (not $150M)."""
    inflow = pd.DataFrame({
        "block_date":   [date(2026, 3, 3)],
        "daily_inflow": [Decimal("300000000")],
        "cum_inflow":   [Decimal("300000000")],
    })
    out = _time_weighted_avg_value(
        _period(), Decimal("0"), inflow,
    )
    # Days 1–2 → $0; days 3–31 → $300M. Mean = 300M × 29 / 31.
    expected = Decimal("300000000") * Decimal("29") / Decimal("31")
    assert out == expected


def test_tw_avg_outflow_mid_month():
    """Position fully exits on day 15 — tw_avg should reflect the half-month presence."""
    inflow = pd.DataFrame({
        "block_date":   [date(2026, 3, 15)],
        "daily_inflow": [Decimal("-100000000")],
        "cum_inflow":   [Decimal("-100000000")],
    })
    out = _time_weighted_avg_value(
        _period(), Decimal("100000000"), inflow,
    )
    # Days 1–14 → $100M; days 15–31 → $0. Mean = 100M × 14 / 31.
    expected = Decimal("100000000") * Decimal("14") / Decimal("31")
    assert out == expected


def test_tw_avg_baseline_subtracted_for_pre_period_flows():
    """Inflows before period.start are baseline, not counted in this period.

    Reproduction: a venue with $50M deposited on Feb 20 (pre-period) and
    no new flows in March should produce tw_avg = value_som ($50M) — the
    Feb deposit is the SoM state, not a March flow.
    """
    inflow = pd.DataFrame({
        "block_date":   [date(2026, 2, 20)],
        "daily_inflow": [Decimal("50000000")],
        "cum_inflow":   [Decimal("50000000")],
    })
    out = _time_weighted_avg_value(
        _period(), Decimal("50000000"), inflow,
    )
    assert out == Decimal("50000000")


# --- erc4626_period_inflow (Centrifuge vault-event inflow) ------------------
#
# For Cat E (RWA_TRANCHE) Centrifuge venues the pipeline derives inflows from
# on-chain ERC-4626 Deposit/Withdraw ``assets`` fields rather than ERC-20
# token-transfer repricing.  The resulting value is stored in
# ``VenueRevenueInputs.erc4626_period_inflow`` and overrides the standard
# ``inflow_timeseries``-based period_inflow and actual_revenue formula.
# Under EoM-locked capped SDE (see ``_capped_sd_revenue_eom_locked``) the
# sd_share is computed from ``value_eom`` directly — the ``inflow_timeseries``
# is no longer involved in the SDE split. It still feeds
# ``_time_weighted_avg_value`` for the CoF-allocation ``tw_avg_value`` field.


def test_erc4626_inflow_overrides_timeseries_for_period_inflow():
    """erc4626_period_inflow replaces the inflow_timeseries cumulative value.

    The token-transfer timeseries shows no movement, but vault events recorded
    a $15 deposit.  period_inflow and actual_revenue should use the vault-event
    amount, not the timeseries zero.
    """
    inputs = VenueRevenueInputs(
        venue=_venue("E8"),
        value_som=Decimal("1_000"),
        value_eom=Decimal("1_020"),
        inflow_timeseries=_empty_inflow(),       # RWA: no movement seen
        erc4626_period_inflow=Decimal("15"),     # vault event: $15 deposit
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.period_inflow == Decimal("15")     # from erc4626_period_inflow
    assert vr.actual_revenue == Decimal("5")     # 1020 − 1000 − 15
    assert vr.revenue == Decimal("5")            # no SDE → all to prime


def test_erc4626_period_inflow_none_falls_back_to_timeseries():
    """erc4626_period_inflow=None → standard path; inflow_timeseries drives period_inflow."""
    inflow_df = pd.DataFrame({
        "block_date":   [date(2026, 3, 10)],
        "daily_inflow": [Decimal("-100")],
        "cum_inflow":   [Decimal("-100")],
    })
    inputs = VenueRevenueInputs(
        venue=_venue("E8"),
        value_som=Decimal("1_000"),
        value_eom=Decimal("950"),
        inflow_timeseries=inflow_df,
        erc4626_period_inflow=None,              # explicit None → standard path
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.period_inflow == Decimal("-100")   # from timeseries
    assert vr.actual_revenue == Decimal("50")    # 950 − 1000 − (−100)


def test_erc4626_negative_implied_yield():
    """A large withdrawal at an intra-period NAV below the SoM NAV produces
    genuine negative implied yield — modelling the March 2026 E8 scenario.

    Mechanism: SOM holds 1000 shares at $1.00 = $1000.
    On day 15, 800 shares exit at $0.99/share → $792 USDC received (exact).
    Remaining 200 shares appreciate to $1.01/share → EOM $202.

    implied_yield = EOM − SOM − inflow = 202 − 1000 − (−792) = −6.
    The $6 loss is because the withdrawn shares were priced at $1.00 at SoM
    but only returned $0.99 each — the NAV slipped slightly before exit.
    """
    inputs = VenueRevenueInputs(
        venue=_venue("E8"),
        value_som=Decimal("1_000"),
        value_eom=Decimal("202"),
        inflow_timeseries=_empty_inflow(),
        erc4626_period_inflow=Decimal("-792"),   # vault event: exact USDC out
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.period_inflow == Decimal("-792")
    assert vr.actual_revenue == Decimal("-6")    # genuine loss
    assert vr.revenue == Decimal("-6")           # no SDE → prime absorbs loss


def test_erc4626_capped_sde_uses_eom_locked_share():
    """ERC-4626 + capped SDE under EoM-locked: vault-event actual_revenue
    is split at sd_share = min(cap, value_eom) / value_eom.
    """
    cap = Decimal("600")
    som = Decimal("1000")
    eom = Decimal("1030")
    inputs = VenueRevenueInputs(
        venue=_venue("E8"),
        value_som=som,
        value_eom=eom,
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("E8", cap),
        erc4626_period_inflow=Decimal("-10"),
    )
    vr = compute_venue_revenue(_period(), inputs)

    # actual_revenue = 1030 − 1000 − (−10) = 40 (vault-event based)
    assert vr.actual_revenue == Decimal("40")
    # EoM-locked sd_share = min(600, 1030) / 1030 = 600/1030 (exact Decimal).
    expected_share = cap / eom
    assert vr.sd_share == expected_share
    assert vr.sd_revenue == Decimal("40") * expected_share
    assert vr.revenue == Decimal("40") * (Decimal("1") - expected_share)


def test_erc4626_with_fixed_sde_routes_all_to_sky():
    """erc4626_period_inflow + kind=fixed SDE → all revenue routes to Sky.
    Fixed SDE has sd_share = 1 regardless of the EoM-locked branch.
    """
    inputs = VenueRevenueInputs(
        venue=_venue("E8"),
        value_som=Decimal("1_000"),
        value_eom=Decimal("1_050"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_fixed("E8"),
        erc4626_period_inflow=Decimal("20"),   # $20 deposit via vault event
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.period_inflow == Decimal("20")
    assert vr.actual_revenue == Decimal("30")     # 1050 − 1000 − 20
    assert vr.sd_share == Decimal("1")            # fixed → 100% Sky
    assert vr.sd_revenue == Decimal("30")
    assert vr.revenue == Decimal("0")             # prime gets nothing


# --- _capped_sd_revenue_eom_locked degenerate / display cases --------------

def test_capped_sde_full_redemption_falls_back_to_som_locked_share():
    """value_eom = 0 with value_som > 0 (entire capped position redeemed
    mid-period): EoM-locked is undefined (min(cap, 0)/0), fall back to
    SoM-locked share so the loss is attributed proportionally rather than
    silently dropping it all on Prime. See ``_capped_sd_revenue_eom_locked``.
    """
    cap = Decimal("600")
    som = Decimal("1_000")
    inputs = VenueRevenueInputs(
        venue=_venue("E8"),
        value_som=som,
        value_eom=Decimal("0"),                  # full redemption
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("E8", cap),
        erc4626_period_inflow=Decimal("-990"),   # $990 out via vault events
    )
    vr = compute_venue_revenue(_period(), inputs)
    # actual_revenue = 0 − 1000 − (−990) = −10 (small intra-period loss)
    assert vr.actual_revenue == Decimal("-10")
    # Falls back to SoM-locked: min(600, 1000) / 1000 = 0.6.
    assert vr.sd_share == Decimal("0.6")
    assert vr.sd_revenue == Decimal("-10") * Decimal("0.6")
    assert vr.revenue == Decimal("-10") * Decimal("0.4")


def test_capped_sde_display_share_nonzero_when_actual_revenue_is_zero():
    """A capped position that happens to break even (actual_revenue = 0)
    should still report its EoM-locked sd_share for display, not 0. The
    Sky/Prime split is well-defined even when the magnitude is zero.
    """
    cap = Decimal("600")
    inputs = VenueRevenueInputs(
        venue=_venue("E8"),
        value_som=Decimal("1_000"),
        value_eom=Decimal("1_000"),              # flat
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("E8", cap),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.actual_revenue == Decimal("0")
    assert vr.sd_share == cap / Decimal("1_000")  # 0.6, not 0
    assert vr.sd_revenue == Decimal("0")


# --- _time_weighted_notional ------------------------------------------------

def _venue_with_notional(
    vid: str,
    schedule: tuple[NotionalScheduleEntry, ...] | None,
) -> Venue:
    base = _venue(vid)
    return Venue(
        id=base.id,
        chain=base.chain,
        token=base.token,
        pricing_category=base.pricing_category,
        underlying=base.underlying,
        label=base.label,
        notional_principal_usd=schedule,
    )


def test_time_weighted_notional_none_returns_zero():
    assert _time_weighted_notional(None, _period()) == Decimal("0")


def test_time_weighted_notional_scalar_constant_through_period():
    """Scalar form (single entry at date.min): constant notional across
    every settlement period."""
    schedule = (NotionalScheduleEntry(start_date=date.min, amount=Decimal("50_000_000")),)
    assert _time_weighted_notional(schedule, _period()) == Decimal("50_000_000")


def test_time_weighted_notional_step_activates_mid_period():
    """Schedule activates mid-period (start_date inside [period.start,
    period.end]). The avg = applicable_days × amount / n_days, where
    applicable_days counts days >= start_date."""
    # Period is 2026-03-01 → 2026-03-31 (31 days).
    # Schedule: $0 before 2026-03-11; $31M from 2026-03-11 onward (21 days).
    schedule = (
        NotionalScheduleEntry(start_date=date(2026, 3, 11), amount=Decimal("31_000_000")),
    )
    # Expected: 21 days × 31M / 31 days = 21_000_000.
    assert _time_weighted_notional(schedule, _period()) == Decimal("21_000_000")


def test_time_weighted_notional_step_down_inside_period():
    """Two-step schedule with a drop mid-period (loan termination). Notional
    is 31M for days 1–10 and 0 from day 11 onward."""
    schedule = (
        NotionalScheduleEntry(start_date=date(2025, 12, 1), amount=Decimal("31_000_000")),
        NotionalScheduleEntry(start_date=date(2026, 3, 11), amount=Decimal("0")),
    )
    # 10 days × 31M + 21 days × 0 = 310M; / 31 days = 10_000_000.
    assert _time_weighted_notional(schedule, _period()) == Decimal("10_000_000")


def test_time_weighted_notional_schedule_after_period_returns_zero():
    """Schedule starts entirely after the period — no notional applies."""
    schedule = (
        NotionalScheduleEntry(start_date=date(2026, 6, 1), amount=Decimal("50_000_000")),
    )
    assert _time_weighted_notional(schedule, _period()) == Decimal("0")


def test_compute_venue_revenue_emits_tw_avg_notional_from_venue_config():
    """End-to-end: a venue with notional_principal_usd configured sees the
    time-weighted value surface on VenueRevenue.tw_avg_notional."""
    schedule = (NotionalScheduleEntry(start_date=date.min, amount=Decimal("50_000_000")),)
    inputs = VenueRevenueInputs(
        venue=_venue_with_notional("E21", schedule),
        value_som=Decimal("0"),
        value_eom=Decimal("0"),
        inflow_timeseries=_empty_inflow(),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.tw_avg_notional == Decimal("50_000_000")


def test_notional_principal_usd_does_not_change_headline_fields():
    """Headline invariance: configuring ``notional_principal_usd`` on a venue
    must NOT change any number that feeds sky_revenue, prime_agent_revenue,
    or monthly_pnl. The field is display-only — it only affects per-venue
    CoF allocation downstream in ``build_monthly_report.py``.

    Pins this by running ``compute_prime_agent_revenue`` on the same venue
    twice — once with notional configured, once without — and asserting every
    headline-relevant field on the resulting VenueRevenue is bit-identical.
    """
    schedule = (
        NotionalScheduleEntry(start_date=date.min, amount=Decimal("50_000_000")),
    )
    # A venue with a non-trivial revenue profile to give the assertions
    # something substantive to compare.
    value_som = Decimal("1_000_000")
    value_eom = Decimal("1_050_000")

    def _run(notional: tuple | None) -> tuple:
        venue = _venue_with_notional("E21", notional)
        inputs = [VenueRevenueInputs(
            venue=venue,
            value_som=value_som,
            value_eom=value_eom,
            inflow_timeseries=_empty_inflow(),
        )]
        total, breakdown = compute_prime_agent_revenue(_period(), inputs)
        return total, breakdown[0]

    total_a, vr_a = _run(None)
    total_b, vr_b = _run(schedule)

    # Headline aggregate (= prime_agent_revenue) is identical.
    assert total_a == total_b

    # Every field that flows into sky_revenue / prime_agent_revenue /
    # monthly_pnl is identical between the two runs.
    for field_name in (
        "venue_id", "label",
        "value_som", "value_eom", "period_inflow",
        "revenue", "actual_revenue", "external_revenue",
        "sd_share", "sd_revenue",
        "br_charge", "sky_direct_shortfall",
    ):
        assert getattr(vr_a, field_name) == getattr(vr_b, field_name), (
            f"{field_name} drifted between notional=None and notional=schedule"
        )

    # The only field that differs is the display one.
    assert vr_a.tw_avg_notional == Decimal("0")
    assert vr_b.tw_avg_notional == Decimal("50_000_000")


# --- fixed_fee_per_capital_event_usd (off-chain redemption fee) ------------

def _venue_with_fee(fee: Decimal, min_transfer: Decimal | None = Decimal("1000000")) -> Venue:
    base = _venue("E10")
    return Venue(
        id=base.id,
        chain=base.chain,
        token=base.token,
        pricing_category=base.pricing_category,
        underlying=base.underlying,
        label=base.label,
        min_transfer_amount_usd=min_transfer,
        fixed_fee_per_capital_event_usd=fee,
    )


def _inflow_with_events(events: list[tuple[date, Decimal]]) -> pd.DataFrame:
    """Build an inflow_timeseries for the test. ``events`` is a list of
    (date, daily_inflow) — cum_inflow is the running sum."""
    cum = Decimal("0")
    rows = []
    for d, v in events:
        cum += v
        rows.append({"block_date": d, "daily_inflow": v, "cum_inflow": cum})
    return pd.DataFrame(rows)


def test_fee_subtracts_15k_per_shaved_amount_event_in_period():
    """BUIDL pattern: subscription minted at $50M − $15K = $49,985K because
    BlackRock takes the $15K fee at the source. Detect via the "shaved
    amount" signature: ``|amount| + fee`` divides cleanly by $1M.
    """
    period = _period()  # 2026-03-01 → 2026-03-31
    # 5 fee-charged subs ($49,985K = clean $50M − $15K) + 2 clean subs
    # (no fee). The clean ones add up to round numbers; the shaved ones do
    # not. Only the shaved ones trigger the fee deduction.
    events = [
        (date(2026, 3, 3),  Decimal("49985000")),    # SHAVED — fee charged
        (date(2026, 3, 7),  Decimal("49985000")),    # SHAVED
        (date(2026, 3, 10), Decimal("50000000")),    # clean — no fee
        (date(2026, 3, 14), Decimal("49985000")),    # SHAVED
        (date(2026, 3, 20), Decimal("49985000")),    # SHAVED
        (date(2026, 3, 25), Decimal("25000000")),    # clean — no fee
        (date(2026, 3, 28), Decimal("49985000")),    # SHAVED
    ]
    inflow = _inflow_with_events(events)
    inputs = VenueRevenueInputs(
        venue=_venue_with_fee(Decimal("15000")),
        value_som=Decimal("0"),
        value_eom=Decimal("324_850_000"),   # value_eom = period_inflow exactly
        inflow_timeseries=inflow,
    )
    vr = compute_venue_revenue(period, inputs)
    # period_inflow = 5×49,985,000 + 50,000,000 + 25,000,000 = $324,925,000
    assert vr.period_inflow == Decimal("324_925_000")
    # gross actual_revenue = 324.85M − 0 − 324.925M = −$75K
    # MINUS 5 × $15K fee = −$75K − $75K = −$150K
    assert vr.actual_revenue == Decimal("-150000")


def test_fee_outside_period_does_not_count():
    """Events outside the settlement period are ignored — only in-period
    fee events count."""
    period = _period()  # 2026-03-01 → 2026-03-31
    events = [
        (date(2026, 2, 5),  Decimal("49985000")),    # pre-period
        (date(2026, 3, 15), Decimal("49985000")),    # in-period — fee
        (date(2026, 4, 12), Decimal("49985000")),    # post-period
    ]
    inflow = _inflow_with_events(events)
    inputs = VenueRevenueInputs(
        venue=_venue_with_fee(Decimal("15000")),
        value_som=Decimal("0"),
        value_eom=Decimal("49_985_000"),
        inflow_timeseries=inflow,
    )
    vr = compute_venue_revenue(period, inputs)
    # period_inflow = only the in-period row = $49.985M
    assert vr.period_inflow == Decimal("49_985_000")
    # gross actual = 49.985M − 0 − 49.985M = $0, MINUS 1 × $15K fee = −$15K
    assert vr.actual_revenue == Decimal("-15000")


def test_fee_skips_clean_round_amounts():
    """Clean round-number mints (no fee charged at source) → no fee
    deduction even though they're in-period capital events."""
    period = _period()
    events = [
        (date(2026, 3, 5),  Decimal("50000000")),
        (date(2026, 3, 12), Decimal("25000000")),
        (date(2026, 3, 22), Decimal("10000000")),
    ]
    inflow = _inflow_with_events(events)
    inputs = VenueRevenueInputs(
        venue=_venue_with_fee(Decimal("15000")),
        value_som=Decimal("0"),
        value_eom=Decimal("85_000_000"),
        inflow_timeseries=inflow,
    )
    vr = compute_venue_revenue(period, inputs)
    # All amounts are clean → no fee detected → actual_revenue = $0
    assert vr.actual_revenue == Decimal("0")


def test_fee_without_min_transfer_raises():
    """Configuring a per-event fee without min_transfer_amount_usd would
    over-count fees on the unfiltered daily yield mints — refuse the call."""
    period = _period()
    inputs = VenueRevenueInputs(
        venue=_venue_with_fee(Decimal("15000"), min_transfer=None),
        value_som=Decimal("0"),
        value_eom=Decimal("0"),
        inflow_timeseries=_empty_inflow(),
    )
    with pytest.raises(ValueError, match="min_transfer_amount_usd"):
        compute_venue_revenue(period, inputs)


def test_fee_with_no_events_in_period_is_a_noop():
    """No redemption events in the period → no fee deduction."""
    period = _period()
    inputs = VenueRevenueInputs(
        venue=_venue_with_fee(Decimal("15000")),
        value_som=Decimal("100_000_000"),
        value_eom=Decimal("100_500_000"),
        inflow_timeseries=_empty_inflow(),
    )
    vr = compute_venue_revenue(period, inputs)
    assert vr.actual_revenue == Decimal("500_000")


def test_fee_routes_to_sky_for_fixed_sde_venue():
    """For fixed-SDE venues (sd_share = 1) the fee flows entirely to Sky.
    Pins the BUIDL economics: Grove pays the fee implicitly via reduced
    Sky-Direct revenue."""
    period = _period()
    # One fee-charged event ($50M sub minted as $49,985K).
    events = [(date(2026, 3, 10), Decimal("49985000"))]
    inputs = VenueRevenueInputs(
        venue=_venue_with_fee(Decimal("15000")),
        value_som=Decimal("100_000_000"),
        value_eom=Decimal("155_100_000"),    # +$5.115M MtM after capital
        inflow_timeseries=_inflow_with_events(events),
        sde_entry=_sde_fixed("E10"),
    )
    vr = compute_venue_revenue(period, inputs)
    # gross actual = 155.1M − 100M − 49.985M = $5,115,000
    # fee = 1 × $15K → actual_revenue = $5,100,000
    # fixed SDE → sd_share = 1 → sd_revenue = $5,100,000 (Sky absorbs the fee)
    assert vr.actual_revenue == Decimal("5_100_000")
    assert vr.sd_revenue == Decimal("5_100_000")
    assert vr.revenue == Decimal("0")


def test_fee_detects_shaved_redemption_event():
    """The shaved-amount heuristic is direction-agnostic — a negative
    daily_inflow with the same signature (|amount| + fee divisible by $1M)
    triggers the fee deduction. Pins that ``abs(amount)`` correctly captures
    both subscription-direction and redemption-direction fee events.
    """
    period = _period()
    # One redemption-direction fee event: ALM gave up $50M of position,
    # received $49,985K back (the $15K fee was shaved at source).
    events = [(date(2026, 3, 10), Decimal("-49985000"))]
    inputs = VenueRevenueInputs(
        venue=_venue_with_fee(Decimal("15000")),
        value_som=Decimal("50_000_000"),
        value_eom=Decimal("0"),
        inflow_timeseries=_inflow_with_events(events),
    )
    vr = compute_venue_revenue(period, inputs)
    # period_inflow = -49,985,000
    # gross actual_revenue = 0 − 50M − (−49,985K) = −$15,000
    # fee = 1 × $15K → actual_revenue = −$30,000
    assert vr.period_inflow == Decimal("-49985000")
    assert vr.actual_revenue == Decimal("-30000")


def test_fee_skips_clean_round_redemption():
    """A clean round-number redemption (no fee charged at source) → no
    fee deduction. Mirrors ``test_fee_skips_clean_round_amounts`` but in
    the negative direction."""
    period = _period()
    events = [(date(2026, 3, 10), Decimal("-50000000"))]
    inputs = VenueRevenueInputs(
        venue=_venue_with_fee(Decimal("15000")),
        value_som=Decimal("50_000_000"),
        value_eom=Decimal("0"),
        inflow_timeseries=_inflow_with_events(events),
    )
    vr = compute_venue_revenue(period, inputs)
    # gross actual = 0 − 50M − (−50M) = $0, NO fee → actual_revenue = $0
    assert vr.actual_revenue == Decimal("0")


def test_fee_with_zero_min_transfer_raises():
    """``min_transfer_amount_usd = 0`` satisfies ``is not None`` but defeats
    the heuristic's intent — the guard must reject it just like None,
    otherwise yield mints in the timeseries could accidentally satisfy the
    shaved-amount test."""
    period = _period()
    inputs = VenueRevenueInputs(
        venue=_venue_with_fee(Decimal("15000"), min_transfer=Decimal("0")),
        value_som=Decimal("0"),
        value_eom=Decimal("0"),
        inflow_timeseries=_empty_inflow(),
    )
    with pytest.raises(ValueError, match="min_transfer_amount_usd"):
        compute_venue_revenue(period, inputs)


def test_fee_and_actual_revenue_override_are_mutually_exclusive():
    """A venue cannot simultaneously have ``actual_revenue_override`` (used
    by the sUSDS-spread closed-form path) and ``fixed_fee_per_capital_event_usd``
    set — the fee heuristic depends on the inflow timeseries that the
    override path doesn't consume. Refuse explicitly via assertion."""
    period = _period()
    inputs = VenueRevenueInputs(
        venue=_venue_with_fee(Decimal("15000")),
        value_som=Decimal("0"),
        value_eom=Decimal("0"),
        inflow_timeseries=_empty_inflow(),
        actual_revenue_override=Decimal("1000"),
    )
    with pytest.raises(AssertionError, match="mutually exclusive"):
        compute_venue_revenue(period, inputs)


def test_fee_with_erc4626_period_inflow_branch():
    """ERC-4626 Centrifuge venues use ``erc4626_period_inflow`` to override
    the period_inflow formula. The fee detection still reads
    ``inputs.inflow_timeseries`` (token-transfer clock) — the two paths
    coexist. Pins that the fee fires from the timeseries even when
    actual_revenue is computed via the vault-event override."""
    period = _period()
    # Token-transfer timeseries has one fee-charged event (Mar 10 $49,985K).
    events = [(date(2026, 3, 10), Decimal("49985000"))]
    inputs = VenueRevenueInputs(
        venue=_venue_with_fee(Decimal("15000")),
        value_som=Decimal("100_000_000"),
        value_eom=Decimal("150_000_000"),
        inflow_timeseries=_inflow_with_events(events),
        erc4626_period_inflow=Decimal("49985000"),   # vault-event matches
    )
    vr = compute_venue_revenue(period, inputs)
    # period_inflow uses the vault-event value ($49,985K).
    # gross actual = 150M − 100M − 49.985M = $15,000
    # fee = 1 × $15K → actual_revenue = $0
    assert vr.period_inflow == Decimal("49985000")
    assert vr.actual_revenue == Decimal("0")


# --- sky_savings_token susds_spread_reimbursement plumbing ---------------
#
# Cat B ``sky_savings_token`` venues (Spark S32 / S37 / S43 / S47 / S51)
# get a 30bps spread reimbursement applied as a Sky Revenue REDUCTION (not
# a Prime Revenue credit). The orchestrator computes
# ``_susds_spread_reimbs[venue.id] = value_som × spread_daily × n_days``
# and injects it onto each VenueRevenue via ``dataclasses.replace``. These
# tests pin the dataclass-field plumbing — the actual orchestrator
# computation is exercised at the integration level.

def test_venue_revenue_susds_spread_reimbursement_defaults_to_zero():
    """Default value is zero for non-sky_savings_token venues so the field is
    safe to sum across the breakdown unconditionally."""
    inputs = VenueRevenueInputs(
        venue=_venue("V1"),
        value_som=Decimal("100_000_000"), value_eom=Decimal("100_000_000"),
        inflow_timeseries=_empty_inflow(),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.susds_spread_reimbursement == Decimal("0")


def test_venue_revenue_susds_spread_reimbursement_preserved_via_dataclasses_replace():
    """The orchestrator injects the Cat B per-venue reimbursement via
    ``dataclasses.replace(vr, susds_spread_reimbursement=X)`` AFTER
    ``compute_venue_revenue`` returns. Regression guard: that field MUST be
    a writeable ``dataclasses.replace`` target and survive the replace
    operation — otherwise the per-venue plumbing → CSV → report breaks
    silently while the aggregate stays correct.
    """
    import dataclasses
    inputs = VenueRevenueInputs(
        venue=_venue("S37"),
        value_som=Decimal("100_000_000"), value_eom=Decimal("100_000_000"),
        inflow_timeseries=_empty_inflow(),
    )
    vr = compute_venue_revenue(_period(), inputs)
    # Mirror the orchestrator: monthly_pnl.py:2929-2937 dataclasses.replace pass.
    spread = Decimal("25_479.45")    # 100M × 30bps / 365 × 31
    vr_updated = dataclasses.replace(vr, susds_spread_reimbursement=spread)
    assert vr_updated.susds_spread_reimbursement == spread
    # All other fields preserved.
    assert vr_updated.venue_id == "S37"
    assert vr_updated.value_som == Decimal("100_000_000")
    assert vr_updated.revenue == vr.revenue
    assert vr_updated.sd_revenue == vr.sd_revenue


def test_venue_revenue_susds_spread_serialised_to_provenance():
    """The compute layer plumbs ``susds_spread_reimbursement`` onto
    ``VenueRevenue``; the Load layer (``write_provenance``) must include
    it in the JSON row so ``settle.load.grove_sheet.compute_sheet_rows``
    can read it back as ``r.get('susds_spread_reimbursement')``.
    Regression guard against the JSON schema drifting out of sync."""
    import json
    import tempfile
    import dataclasses
    from pathlib import Path

    from settle.domain.monthly_pnl import MonthlyPnL
    from settle.load.provenance import write_provenance

    inputs = VenueRevenueInputs(
        venue=_venue("S37"),
        value_som=Decimal("100_000_000"), value_eom=Decimal("100_000_000"),
        inflow_timeseries=_empty_inflow(),
    )
    vr = compute_venue_revenue(_period(), inputs)
    vr = dataclasses.replace(vr, susds_spread_reimbursement=Decimal("25479.45"))

    pnl = MonthlyPnL(
        prime_id="spark",
        month=_period().start.replace(day=1),
        period=_period(),
        sky_revenue=Decimal("0"),
        agent_rate=Decimal("0"),
        prime_agent_revenue=Decimal("0"),
        monthly_pnl=Decimal("0"),
        venue_breakdown=[vr],
        pin_blocks_som={Chain.ETHEREUM: 0},
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = write_provenance(pnl, Path(tmp) / "provenance.json")
        with path.open() as f:
            prov = json.load(f)
    rows = prov["venue_breakdown"]
    assert len(rows) == 1
    assert "susds_spread_reimbursement" in rows[0], (
        "provenance.json venue_breakdown rows must carry "
        "susds_spread_reimbursement — settle.load.grove_sheet reads it "
        "via r.get(...)"
    )
    assert Decimal(rows[0]["susds_spread_reimbursement"]) == Decimal("25479.45")


def test_monthly_pnl_susds_spread_reimbursement_aggregates_per_venue():
    """The headline ``MonthlyPnL.susds_spread_reimbursement`` is the
    aggregate sky-revenue reduction; it must include both the per-venue
    Cat B amounts AND any Curve LP + PSM3 components folded in by the
    orchestrator. Pins that the field exists and can carry the aggregate
    independently of the per-venue ``VenueRevenue.susds_spread_reimbursement``
    fields."""
    from settle.domain.monthly_pnl import MonthlyPnL

    # Two Cat B venues, each reimbursed $10K; plus $5K Curve + $3K PSM3.
    breakdown = [
        compute_venue_revenue(_period(), VenueRevenueInputs(
            venue=_venue("S37"),
            value_som=Decimal("100_000_000"), value_eom=Decimal("100_000_000"),
            inflow_timeseries=_empty_inflow(),
        )),
        compute_venue_revenue(_period(), VenueRevenueInputs(
            venue=_venue("S43"),
            value_som=Decimal("100_000_000"), value_eom=Decimal("100_000_000"),
            inflow_timeseries=_empty_inflow(),
        )),
    ]
    import dataclasses
    breakdown = [
        dataclasses.replace(breakdown[0], susds_spread_reimbursement=Decimal("10000")),
        dataclasses.replace(breakdown[1], susds_spread_reimbursement=Decimal("10000")),
    ]
    # Mirrors monthly_pnl.py:2943-2947 aggregation formula.
    curve = Decimal("5000")
    psm3 = Decimal("3000")
    total_reimb = sum(
        (vr.susds_spread_reimbursement for vr in breakdown), Decimal("0"),
    ) + curve + psm3
    assert total_reimb == Decimal("28000")

    sky_rev = Decimal("100000") - total_reimb
    pnl = MonthlyPnL(
        prime_id="spark", month=_period().start.replace(day=1), period=_period(),
        sky_revenue=sky_rev,
        agent_rate=Decimal("0"), prime_agent_revenue=Decimal("0"),
        # MonthlyPnL.__post_init__ enforces:
        #   monthly_pnl ≡ prime_rev + agent_rate + distribution_rewards − sky_rev
        monthly_pnl=-sky_rev,
        venue_breakdown=breakdown,
        pin_blocks_som={Chain.ETHEREUM: 0},
        curve_susds_spread=curve, psm3_susds_spread=psm3,
        susds_spread_reimbursement=total_reimb,
    )
    # Headline aggregate must equal Σ per-venue + curve + psm3.
    assert pnl.susds_spread_reimbursement == Decimal("28000")
    assert pnl.susds_spread_reimbursement == (
        sum((vr.susds_spread_reimbursement for vr in pnl.venue_breakdown), Decimal("0"))
        + pnl.curve_susds_spread + pnl.psm3_susds_spread
    )


def _nominal_series(values, daily_rate):
    """Nominal accrual over a per-day principal series: each day charges
    ``V_d × daily_rate`` and the days are summed — no compounding inside the
    period (2026-09-01; the 20bps legs mirror the equally-nominal BR charge
    they offset)."""
    return sum((v * daily_rate for v in values), Decimal("0"))


# --- _susds_cat_b_spread_reimb daily integration -------------------------
#
# Rule 5 consistency: the Cat B sky_savings_token 30bps reimbursement now
# uses ``Σ_d V_d × spread_daily`` like the Curve LP and PSM3 paths, instead
# of the prior flat ``value_som × n_days × spread_daily`` approximation.
# These tests pin the helper's daily-integration semantics.

def test_susds_cat_b_spread_reimb_stable_position_matches_flat_formula():
    """With NO inflows the daily integration reduces to ``V × rate × n/365``
    — the reimbursement is NOMINAL (2026-09-01), matching the BR charge it
    offsets, so it equals the flat formula exactly."""
    from settle.compute.monthly_pnl import _susds_cat_b_spread_reimb
    from settle.compute._helpers import apr_daily, daily_compounding_factor
    from settle.compute.sky_revenue import BASE_RATE_OVER_SSR
    spread_daily = apr_daily(BASE_RATE_OVER_SSR)
    value_som = Decimal("100_000_000")
    period = _period()  # 31 days
    expected_flat = value_som * spread_daily * Decimal(period.n_days)
    # Empty inflow_ts → V_d = value_som every day.
    result = _susds_cat_b_spread_reimb(value_som, _empty_inflow(), period)
    assert abs(result - expected_flat) < Decimal("1e-9")


def test_susds_cat_b_spread_reimb_mid_period_deposit_integrates_correctly():
    """The bug the helper fixes: a mid-period sUSDS deposit. Old flat
    formula (value_som × n_days × spread_daily) under-counted by
    ``Σ_{d>=deposit_day} (deposit_amount) × spread_daily``. Daily
    integration correctly attributes the spread to the days the prime
    actually held the higher balance.

    Scenario: $100M SoM, +$50M deposit on Mar 15 (day 15 of 31).
        Days 1-14: V_d = $100M (14 days)
        Days 15-31: V_d = $150M (17 days)
        Σ V_d = 14×$100M + 17×$150M = $1.4B + $2.55B = $3.95B
        flat = $100M × 31 = $3.1B
        Δ = $850M × spread_daily (= +$850M × 30bps / 365 ≈ +$699)
    """
    from settle.compute.monthly_pnl import _susds_cat_b_spread_reimb
    from settle.compute._helpers import apr_daily, daily_compounding_factor
    from settle.compute.sky_revenue import BASE_RATE_OVER_SSR
    spread_daily = apr_daily(BASE_RATE_OVER_SSR)
    value_som = Decimal("100_000_000")
    period = _period()
    # Mar 15 = day 15 of 31. Construct cum_inflow timeseries.
    deposit_day = date(2026, 3, 15)
    inflow_ts = pd.DataFrame({
        "block_date":   [deposit_day],
        "daily_inflow": [Decimal("50_000_000")],
        "cum_inflow":   [Decimal("50_000_000")],
    })
    result = _susds_cat_b_spread_reimb(value_som, inflow_ts, period)

    # V_d = 14×$100M then 17×$150M, summed nominally.
    expected = _nominal_series(
        [Decimal("100_000_000")] * 14 + [Decimal("150_000_000")] * 17, spread_daily,
    )
    assert abs(result - expected) < Decimal("1e-9")

    # Sanity: result strictly greater than flat formula (more days at $150M).
    flat = value_som * spread_daily * Decimal(period.n_days)
    assert result > flat
    # Δ = $850M × spread_daily (~$699 at 30bps), exactly.
    delta = result - flat
    assert abs(delta - Decimal("850_000_000") * spread_daily) < Decimal("1e-9")


def test_susds_cat_b_spread_reimb_mid_period_withdrawal_integrates_correctly():
    """Inverse case: $150M SoM, −$50M withdrawal on Mar 15. Daily
    integration produces a smaller spread than the flat formula (the prime
    held less for the second half of the month), so sky_revenue gets a
    smaller reduction → sky earns more. Flat formula over-counted."""
    from settle.compute.monthly_pnl import _susds_cat_b_spread_reimb
    from settle.compute._helpers import apr_daily, daily_compounding_factor
    from settle.compute.sky_revenue import BASE_RATE_OVER_SSR
    spread_daily = apr_daily(BASE_RATE_OVER_SSR)
    value_som = Decimal("150_000_000")
    period = _period()
    withdrawal_day = date(2026, 3, 15)
    inflow_ts = pd.DataFrame({
        "block_date":   [withdrawal_day],
        "daily_inflow": [Decimal("-50_000_000")],
        "cum_inflow":   [Decimal("-50_000_000")],
    })
    result = _susds_cat_b_spread_reimb(value_som, inflow_ts, period)
    # V_d = 14×$150M then 17×$100M, summed nominally.
    expected = _nominal_series(
        [Decimal("150_000_000")] * 14 + [Decimal("100_000_000")] * 17, spread_daily,
    )
    assert abs(result - expected) < Decimal("1e-9")
    flat = value_som * spread_daily * Decimal(period.n_days)
    assert result < flat   # daily integration < flat for withdrawals


def test_susds_cat_b_spread_reimb_zero_position_returns_zero():
    """Degenerate case: no position throughout. Stable-zero AND empty inflow_ts
    short-circuit to zero (no `Σ` to compute)."""
    from settle.compute.monthly_pnl import _susds_cat_b_spread_reimb
    assert _susds_cat_b_spread_reimb(Decimal("0"), _empty_inflow(), _period()) == Decimal("0")
    assert _susds_cat_b_spread_reimb(Decimal("0"), None, _period()) == Decimal("0")


def test_susds_cat_b_spread_reimb_clamps_negative_daily_position():
    """If a transient accounting asymmetry produces a negative V_d (same
    cause as the ``tw_avg_value`` clamp — external arrival excluded from
    capital_net, outbound deployment included), the spread integration
    clamps those days to zero so the reimbursement stays non-negative."""
    from settle.compute.monthly_pnl import _susds_cat_b_spread_reimb
    from settle.compute._helpers import apr_daily, daily_compounding_factor
    from settle.compute.sky_revenue import BASE_RATE_OVER_SSR
    spread_daily = apr_daily(BASE_RATE_OVER_SSR)
    value_som = Decimal("50_000_000")
    period = _period()
    # Withdrawal larger than value_som on day 5 → V_d would go negative.
    inflow_ts = pd.DataFrame({
        "block_date":   [date(2026, 3, 5)],
        "daily_inflow": [Decimal("-80_000_000")],
        "cum_inflow":   [Decimal("-80_000_000")],
    })
    result = _susds_cat_b_spread_reimb(value_som, inflow_ts, period)
    # Days 1-4: V_d = $50M. Days 5-31: V_d = -$30M → clamped, contribute 0.
    expected = _nominal_series([Decimal("50_000_000")] * 4, spread_daily)
    assert abs(result - expected) < Decimal("1e-9")
    assert result > Decimal("0")    # always non-negative


# --- actual_revenue_adjustment (Case-2 sUSDS depositor-SSR removal) ---------

def test_actual_revenue_adjustment_subtracts_from_formula_mtm():
    """S32-shaped: raw MtM on the full sUSDS balance includes the
    depositor-sourced spUSDC slice's SSR accrual; the caller passes the
    negated daily-integrated depositor SSR as ``actual_revenue_adjustment``
    so only the debt-sourced appreciation is booked."""
    inputs = VenueRevenueInputs(
        venue=_venue(), value_som=Decimal("1_000_000_000"),
        value_eom=Decimal("1_006_000_000"),
        inflow_timeseries=_empty_inflow(),
        actual_revenue_adjustment=Decimal("-2_000_000"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    # raw MtM = 6M; depositor SSR removed = 2M → booked 4M
    assert vr.revenue == Decimal("4_000_000")
    assert vr.period_inflow == Decimal("0")


def test_actual_revenue_adjustment_composes_with_period_inflow():
    inflow_df = pd.DataFrame({
        "block_date":   [date(2026, 3, 10)],
        "daily_inflow": [Decimal("50_000_000")],
        "cum_inflow":   [Decimal("50_000_000")],
    })
    inputs = VenueRevenueInputs(
        venue=_venue(), value_som=Decimal("1_000_000_000"),
        value_eom=Decimal("1_056_000_000"),
        inflow_timeseries=inflow_df,
        actual_revenue_adjustment=Decimal("-2_000_000"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    # (1.056B − 1.0B) − 50M inflow = 6M raw, − 2M adjustment = 4M
    assert vr.revenue == Decimal("4_000_000")
    assert vr.period_inflow == Decimal("50_000_000")


def test_actual_revenue_adjustment_rejects_override_combination():
    """The override bypasses the MtM formula — a non-zero adjustment there
    would be silently dropped. Must assert loudly instead."""
    inputs = VenueRevenueInputs(
        venue=_venue(), value_som=Decimal("100"), value_eom=Decimal("200"),
        inflow_timeseries=_empty_inflow(),
        actual_revenue_override=Decimal("0"),
        actual_revenue_adjustment=Decimal("-1"),
    )
    with pytest.raises(AssertionError, match="mutually exclusive"):
        compute_venue_revenue(_period(), inputs)


# --- _savings_v2_depositor_ssr (Case-2 daily integration) -------------------

class _StubSv2Source:
    """ISavingsV2DeployedSource stub returning a fixed TA per block."""
    def __init__(self, ta_by_block: dict[int, Decimal]):
        self.ta_by_block = ta_by_block
        self.calls: list[int] = []

    def at_block(self, block: int) -> Decimal:
        self.calls.append(block)
        return self.ta_by_block.get(block, Decimal("0"))


class _StubDayResolver:
    """Maps each EoD datetime to a synthetic block = day-of-month."""
    def block_at_or_before(self, chain: str, ts) -> int:
        return ts.day


def _ssr_df(apy: str = "0.045") -> pd.DataFrame:
    return pd.DataFrame({
        "effective_date": [date(2025, 1, 1)],
        "ssr_apy":        [Decimal(apy)],
    })


def test_savings_v2_depositor_ssr_integrates_daily():
    """Flat $300M TA all month at 4.5% SSR, compounded (2026-08-24):
    ``300M × ((1+f)^31 − 1)``."""
    from settle.compute.monthly_pnl import _savings_v2_depositor_ssr
    from settle.compute._helpers import apr_daily, daily_compounding_factor
    ta = Decimal("300_000_000")
    src = _StubSv2Source({d: ta for d in range(1, 32)})
    total = _savings_v2_depositor_ssr(src, _StubDayResolver(), _ssr_df(), _period())
    _f = daily_compounding_factor(Decimal("0.045"))
    expected = ta * ((1 + _f) ** 31 - 1)
    assert abs(total - expected) < Decimal("1e-9")
    assert len(src.calls) == 31


def test_savings_v2_depositor_ssr_zero_vault_contributes_nothing():
    """Pre-deployment month: TA = 0 every day → total 0, no warnings."""
    from settle.compute.monthly_pnl import _savings_v2_depositor_ssr
    src = _StubSv2Source({})
    total = _savings_v2_depositor_ssr(src, _StubDayResolver(), _ssr_df(), _period())
    assert total == Decimal("0")


def test_savings_v2_depositor_ssr_carries_forward_on_transient_zero():
    """A 0 read after a non-zero day = suspected RPC failure → carry the
    previous day's TA forward (conservative: keeps the carve-out whole)."""
    from settle.compute.monthly_pnl import _savings_v2_depositor_ssr
    from settle.compute._helpers import apr_daily, daily_compounding_factor
    ta = Decimal("300_000_000")
    # Day 15 missing from the map → at_block returns 0 → carry-forward.
    src = _StubSv2Source({d: ta for d in range(1, 32) if d != 15})
    total = _savings_v2_depositor_ssr(src, _StubDayResolver(), _ssr_df(), _period())
    _f = daily_compounding_factor(Decimal("0.045"))
    expected = ta * ((1 + _f) ** 31 - 1)      # compounds (2026-08-24)
    assert abs(total - expected) < Decimal("1e-9")  # day 15 carried forward


def test_case3b_adjustment_plus_external_rebooks_susds_leg_to_prime():
    """Case 3b composition (S24-shaped): the sUSDS-leg SSR ``x`` embedded
    in the LP MtM is removed from the SDE-eligible pot
    (``adjustment = −x``) and re-booked 100% prime (``external = +x``).
    For a fixed-SDE venue (sd_share = 1): Sky's sd_revenue = raw − x,
    prime books exactly +x."""
    raw_mtm = Decimal("74_000")   # full LP MtM incl. sUSDS-leg SSR
    x = Decimal("37_000")         # sUSDS-leg SSR portion
    inputs = VenueRevenueInputs(
        venue=_venue("S24-like"),
        value_som=Decimal("50_000_000"),
        value_eom=Decimal("50_000_000") + raw_mtm,
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_fixed("S24-like"),
        actual_revenue_adjustment=-x,
        external_revenue=x,
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.actual_revenue == raw_mtm - x          # SDE-eligible pot
    assert vr.sd_revenue == raw_mtm - x              # 100% of pot to Sky
    assert vr.revenue == x                           # prime books the SSR
