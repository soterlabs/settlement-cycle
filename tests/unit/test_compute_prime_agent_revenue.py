"""Unit tests for `settle.compute.prime_agent_revenue`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from settle.compute.prime_agent_revenue import (
    VenueRevenueInputs,
    _time_weighted_avg_value,
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
    """Capped SDE: SDE portion of actual_revenue routes to Sky proportionally,
    external_revenue still 100% to prime."""
    inputs = VenueRevenueInputs(
        venue=_venue("SD"),
        value_som=Decimal("100"), value_eom=Decimal("200"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("SD", Decimal("50")),  # sd_share = 50/100 = 0.5
        external_revenue=Decimal("30"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    # actual_revenue = 100, sd_share = 0.5 → Sky gets 50, prime gets 50 + 30 = 80
    assert vr.actual_revenue == Decimal("100")
    assert vr.sd_share == Decimal("0.5")
    assert vr.sd_revenue == Decimal("50")
    assert vr.external_revenue == Decimal("30")
    assert vr.revenue == Decimal("80")


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
# The ``inflow_timeseries`` (token-transfer clock) is still passed to
# ``_daily_capped_sd_revenue`` so SDE cap-weighting uses a consistent clock.


def _daily_sde_values(period: Period, value: Decimal) -> pd.DataFrame:
    """Constant daily position value for the full period — minimal sde_daily_values stub."""
    from datetime import timedelta
    dates, vals = [], []
    d = period.start
    while d <= period.end:
        dates.append(d)
        vals.append(value)
        d += timedelta(days=1)
    return pd.DataFrame({"block_date": dates, "uncapped_value": vals})


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


def test_erc4626_capped_sde_extra_delta_splits_at_som_sd_share():
    """For capped SDE + erc4626_period_inflow: the delta between vault-event
    actual_revenue and the RWA (token-transfer) actual_revenue is split using
    the SOM sd_share (= min(cap, value_som) / value_som).

    Setup
    -----
    value_som=1000, value_eom=1030, cap=600
    inflow_timeseries: empty (token-transfer sees no flows)
    erc4626_period_inflow=−10 (vault events record a $10 net withdrawal)
    sde_daily_values: constant 1030 across all 31 days

    RWA path (what _daily_capped_sd_revenue sees)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    _rwa_actual_revenue = 1030 − 1000 − 0 = 30
    _daily_capped_sd_revenue: only day 1 has daily_rev=30 (1030−1000),
    subsequent days are flat; sd_share_1 = 600/1030
    → sd_revenue_rwa = 30 × 600/1030

    Vault-event path
    ~~~~~~~~~~~~~~~~
    actual_revenue = 1030 − 1000 − (−10) = 40
    delta = 40 − 30 = 10
    SOM sd_share = min(600, 1000) / 1000 = 0.6
    sd_revenue = sd_revenue_rwa + 10 × 0.6 = sd_revenue_rwa + 6
    prime_revenue = 40 − sd_revenue = 34 − sd_revenue_rwa
    (prime gets 40% of the $10 delta = $4; Sky gets 60% = $6)
    """
    period = _period()
    cap = Decimal("600")
    som = Decimal("1000")
    inputs = VenueRevenueInputs(
        venue=_venue("E8"),
        value_som=som,
        value_eom=Decimal("1030"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("E8", cap),
        sde_daily_values=_daily_sde_values(period, Decimal("1030")),
        erc4626_period_inflow=Decimal("-10"),
    )
    vr = compute_venue_revenue(period, inputs)

    assert vr.actual_revenue == Decimal("40")   # vault-event based

    # SOM sd_share = min(600, 1000) / 1000 = 0.6
    som_sd_share = min(cap, som) / som
    # sd_revenue_rwa: only the day-1 jump contributes (days 2–31 flat)
    expected_sd_rwa = Decimal("30") * cap / Decimal("1030")
    expected_sd = expected_sd_rwa + Decimal("10") * som_sd_share
    assert vr.sd_revenue == expected_sd

    # prime gets its RWA share plus (1 − SOM sd_share) × delta
    expected_prime = Decimal("30") - expected_sd_rwa + Decimal("10") * (1 - som_sd_share)
    assert vr.revenue == expected_prime


def test_erc4626_with_fixed_sde_uses_som_locked_share():
    """erc4626_period_inflow + kind=fixed SDE → all revenue routes to Sky.

    The capped/daily branch is only taken when ``kind == 'capped'`` AND
    ``sde_daily_values`` is provided.  Fixed SDE falls through to
    ``_sd_share_at_som`` which returns 1.0, so the entire vault-event-based
    actual_revenue goes to Sky and prime_revenue is zero.

    This pins that the erc4626 branch composes correctly with fixed SDE:
    the inflow override still applies, but the split rule is unchanged.
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


def test_erc4626_capped_without_daily_values_falls_back_to_som_locked():
    """erc4626_period_inflow + capped SDE but NO sde_daily_values → SoM-locked
    share.  Without the per-day timeseries the function can't run the daily
    cap-weighting; the fallback locks sd_share at SoM (= cap/value_som).

    The vault-event inflow override still applies to actual_revenue, but the
    split uses the simple SoM share rather than the special "all delta to SDE"
    routing from the daily branch.
    """
    inputs = VenueRevenueInputs(
        venue=_venue("E8"),
        value_som=Decimal("1_000"),
        value_eom=Decimal("1_050"),
        inflow_timeseries=_empty_inflow(),
        sde_entry=_sde_capped("E8", Decimal("400")),
        sde_daily_values=None,                  # ← explicit None → fallback
        erc4626_period_inflow=Decimal("20"),
    )
    vr = compute_venue_revenue(_period(), inputs)
    assert vr.actual_revenue == Decimal("30")    # 1050 − 1000 − 20

    # sd_share locked at SoM: min(400, 1000) / 1000 = 0.4
    expected_share = Decimal("400") / Decimal("1000")
    assert vr.sd_share == expected_share
    assert vr.sd_revenue == Decimal("30") * expected_share
    assert vr.revenue == Decimal("30") * (Decimal("1") - expected_share)
