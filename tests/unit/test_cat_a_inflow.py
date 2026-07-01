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

from replay.mock_sources import MockBalanceSource


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


def test_cat_a_principal_return_override_reclassifies_external_as_capital(
    config_dir: Path,
):
    """An inflow nominally from an external source whose (date, amount)
    matches a principal-return override is reclassified as capital, not
    yield. This is how tri-party loan principal corrections (e.g., the
    Anchorage S23 $5M Dec-19-2025 partial-principal return) avoid being
    over-counted as off-pool yield."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    # Two inflows from the same external source on different dates:
    #   - 2026-03-05: $891,780 (interest sweep — should remain yield)
    #   - 2026-03-15: $5,000,000 (principal return — overridden to capital)
    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 5),   date(2026, 3, 15)],
        "counterparty": [cp_external,        cp_external],
        "signed_amount":[Decimal("891780"),  Decimal("5000000")],
    })
    overrides = {cp_external: [(date(2026, 3, 15), Decimal("5000000"))]}

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
        principal_return_overrides=overrides,
    )
    # Only the $5M principal-return row is capital; the $890K interest sweep
    # stays excluded (passes through as yield/revenue).
    assert out["daily_inflow"].sum() == Decimal("5000000")


def test_cat_a_principal_return_override_within_dollar_tolerance(
    config_dir: Path,
):
    """Match tolerates ±$1 of rounding noise (per-token decimal scaling
    from on-chain data). $5,000,000.50 vs override $5,000,000 still
    matches."""
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

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
        principal_return_overrides=overrides,
    )
    assert out["daily_inflow"].sum() == Decimal("5000000.50")


def test_cat_a_principal_return_override_misses_on_amount_mismatch(
    config_dir: Path,
):
    """Override does NOT match if the amount is too different — the inflow
    stays classified as yield (excluded from capital). Guards against
    accidentally swallowing an unexpected inflow."""
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

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
        principal_return_overrides=overrides,
    )
    # No match → flow is treated as yield (excluded from capital).
    assert out.empty


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


# ---------------------------------------------------------------------------
# Paired-principal-cap classifier (display-only EOA round-trip pattern).
# ---------------------------------------------------------------------------
#
# Background: a display-only EOA venue tracks principal-out from the ALM to
# an off-protocol address (e.g. FalconX). When the round-trip return lands
# at the anchor Cat A venue via ``paired_source``, the classifier splits
# each inflow into capital (up to the cumulative principal-out) and yield
# (the excess) — so any spread captured during the OOB trip is realized
# as revenue at the anchor when the cash arrives.


def _paired_cap_series(events: list[tuple[date, Decimal]]) -> pd.DataFrame:
    """Build a cum-principal-out DataFrame matching
    ``directed_inflow_timeseries`` shape: ``[block_date, cum_inflow]``."""
    rows = []
    running = Decimal("0")
    for d, amt in events:
        running += amt
        rows.append({"block_date": d, "cum_inflow": running})
    return pd.DataFrame(rows)


def test_paired_cap_return_under_cap_is_all_capital(config_dir: Path):
    """Principal-out $50M; return $40M (under cap) → all $40M classified as
    capital, no yield. Mirrors a partial-return mid-period."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_paired = _bytes20("0x94b398acb2fce988871218221ea6a4a2b26cccbc")
    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":    [date(2026, 3, 10)],
        "counterparty":  [cp_paired],
        "signed_amount": [Decimal("40000000")],   # $40M return
    })
    cap_series = _paired_cap_series([(date(2026, 3, 1), Decimal("50000000"))])

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources=set(),
        paired_principal_caps={cp_paired: cap_series},
    )
    assert out["daily_inflow"].sum() == Decimal("40000000")


def test_paired_cap_return_exactly_at_cap_is_all_capital(config_dir: Path):
    """Principal-out $50M; return exactly $50M → all capital, no yield.
    The cap edge case where principal returns at par with no spread."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_paired = _bytes20("0x94b398acb2fce988871218221ea6a4a2b26cccbc")
    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":    [date(2026, 3, 10)],
        "counterparty":  [cp_paired],
        "signed_amount": [Decimal("50000000")],
    })
    cap_series = _paired_cap_series([(date(2026, 3, 1), Decimal("50000000"))])

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources=set(),
        paired_principal_caps={cp_paired: cap_series},
    )
    assert out["daily_inflow"].sum() == Decimal("50000000")


def test_paired_cap_excess_over_cap_is_yield(config_dir: Path):
    """Principal-out $50M; return $50.12M (single event over the cap) →
    $50M classified as capital, $120k excluded (becomes yield/revenue at
    the anchor). This is the FalconX OOB spread realization case."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_paired = _bytes20("0x94b398acb2fce988871218221ea6a4a2b26cccbc")
    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":    [date(2026, 3, 10)],
        "counterparty":  [cp_paired],
        "signed_amount": [Decimal("50120000")],
    })
    cap_series = _paired_cap_series([(date(2026, 3, 1), Decimal("50000000"))])

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources=set(),
        paired_principal_caps={cp_paired: cap_series},
    )
    # Only $50M is capital; the $120k excess is excluded → becomes revenue
    # at the anchor (Δvalue − $50M of capital_inflow = $120k surplus).
    assert out["daily_inflow"].sum() == Decimal("50000000")


def test_paired_cap_progressive_consumption(config_dir: Path):
    """Two principal-out events ($30M + $20M = $50M cap) and two returns
    ($20M then $30.12M). First return is fully under cap → $20M capital.
    Second return uses the remaining $30M of cap → $30M capital + $120k
    yield. Total capital classified: $50M; total yield: $120k."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_paired = _bytes20("0x94b398acb2fce988871218221ea6a4a2b26cccbc")
    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":    [date(2026, 3, 5),         date(2026, 3, 20)],
        "counterparty":  [cp_paired,                cp_paired],
        "signed_amount": [Decimal("20000000"),      Decimal("30120000")],
    })
    cap_series = _paired_cap_series([
        (date(2026, 3, 1), Decimal("30000000")),   # first principal-out
        (date(2026, 3, 15), Decimal("20000000")),  # second principal-out
    ])

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources=set(),
        paired_principal_caps={cp_paired: cap_series},
    )
    # $20M + $30M = $50M capital classified; the trailing $120k is yield.
    assert out["daily_inflow"].sum() == Decimal("50000000")


def test_paired_cap_inflow_with_zero_cap_is_all_yield(config_dir: Path):
    """A return arrives before any principal has been sent out (cap = $0).
    All of it is yield → no capital row. Mirrors a counterparty paying
    yield prior to the principal-out leg (unusual but possible if mis-
    timed). Defensive: guarantees the cap never goes negative."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_paired = _bytes20("0x94b398acb2fce988871218221ea6a4a2b26cccbc")
    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":    [date(2026, 3, 10)],
        "counterparty":  [cp_paired],
        "signed_amount": [Decimal("100000")],
    })
    # Empty cap series → cum_principal_out is 0 at all dates.
    cap_series = pd.DataFrame({"block_date": [], "cum_inflow": []})

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources=set(),
        paired_principal_caps={cp_paired: cap_series},
    )
    # All yield → no capital row at all.
    assert out.empty


def test_merge_cap_series_sums_two_cumulative_series():
    """Collision handler for the case where two display-only venues share
    the same ``paired_source``: the pooled cap is the sum of each leg's
    cumulative principal-out at every date — not just one leg's. Pins the
    arithmetic: at any date, the merged ``cum_inflow`` equals the sum of
    the carry-forward values of both inputs."""
    from settle.compute.monthly_pnl import _merge_cap_series

    df1 = pd.DataFrame({
        "block_date": [date(2026, 3, 1), date(2026, 3, 10)],
        "cum_inflow": [Decimal("30000000"), Decimal("50000000")],
    })
    df2 = pd.DataFrame({
        "block_date": [date(2026, 3, 5), date(2026, 3, 15)],
        "cum_inflow": [Decimal("20000000"), Decimal("35000000")],
    })

    merged = _merge_cap_series(df1, df2)

    by_date = {r["block_date"]: r["cum_inflow"] for _, r in merged.iterrows()}
    # Mar 1: df1=$30M, df2=$0 (no row yet) → $30M
    assert by_date[date(2026, 3, 1)]  == Decimal("30000000")
    # Mar 5: df1=$30M (carry-forward), df2=$20M → $50M
    assert by_date[date(2026, 3, 5)]  == Decimal("50000000")
    # Mar 10: df1=$50M, df2=$20M (carry-forward) → $70M
    assert by_date[date(2026, 3, 10)] == Decimal("70000000")
    # Mar 15: df1=$50M (carry-forward), df2=$35M → $85M
    assert by_date[date(2026, 3, 15)] == Decimal("85000000")


def test_merge_cap_series_handles_empty_inputs():
    """Defensive: merging with an empty frame returns the other; merging
    two empties returns an empty. Avoids KeyError on the union step when
    one display-only venue has had no principal-out activity yet."""
    from settle.compute.monthly_pnl import _merge_cap_series

    df = pd.DataFrame({
        "block_date": [date(2026, 3, 1)],
        "cum_inflow": [Decimal("100")],
    })
    empty = pd.DataFrame({"block_date": [], "cum_inflow": []})

    assert _merge_cap_series(df, empty).equals(df)
    assert _merge_cap_series(empty, df).equals(df)
    assert _merge_cap_series(empty, empty).empty


def test_paired_cap_pooled_collision_consumes_summed_cap(config_dir: Path):
    """End-to-end: when two display-only EOAs share the same
    ``paired_source``, returns from that counterparty consume the SUMMED
    principal-out cap. A $80M return when leg-A has $50M out and leg-B
    has $30M out should classify all $80M as capital (pooled cap = $80M),
    not just one leg's $50M."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_paired = _bytes20("0x94b398acb2fce988871218221ea6a4a2b26cccbc")

    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":    [date(2026, 3, 20)],
        "counterparty":  [cp_paired],
        "signed_amount": [Decimal("80000000")],
    })

    # Build the pooled cap as if it had been merged by the orchestrator's
    # collision handler from two legs ($50M + $30M = $80M).
    from settle.compute.monthly_pnl import _merge_cap_series
    leg_a = _paired_cap_series([(date(2026, 3, 1), Decimal("50000000"))])
    leg_b = _paired_cap_series([(date(2026, 3, 10), Decimal("30000000"))])
    pooled = _merge_cap_series(leg_a, leg_b)

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources=set(),
        paired_principal_caps={cp_paired: pooled},
    )
    # Full $80M is within pooled cap → all capital, no yield.
    assert out["daily_inflow"].sum() == Decimal("80000000")


def test_paired_cap_non_paired_counterparty_unaffected(config_dir: Path):
    """When a different (non-paired) counterparty is in the same frame,
    its inflows follow the existing classification (external vs capital);
    the paired-cap only affects rows from the paired_source. Pins the
    isolation between the two classification paths."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_paired = _bytes20("0x94b398acb2fce988871218221ea6a4a2b26cccbc")
    cp_internal = _bytes20("0x37305b1cd40574e4c5ce33f8e8306be057fd7341")  # PSM
    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":    [date(2026, 3, 5),       date(2026, 3, 10)],
        "counterparty":  [cp_internal,            cp_paired],
        "signed_amount": [Decimal("1000000"),     Decimal("50120000")],
    })
    cap_series = _paired_cap_series([(date(2026, 3, 1), Decimal("50000000"))])

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources=set(),
        paired_principal_caps={cp_paired: cap_series},
    )
    # Capital = $1M (internal counterparty, untouched) + $50M (paired,
    # capped) = $51M. The $120k excess from the paired return is yield.
    assert out["daily_inflow"].sum() == Decimal("51000000")


# --- yield_reversal_overrides (outflow mirror) -------------------------------

def test_cat_a_yield_reversal_override_excludes_outflow_from_capital(
    config_dir: Path,
):
    """An OUTFLOW to an external source whose (date, |amount|) matches a
    yield-reversal override is excluded from the capital frame, so it nets
    against the source's inflows in revenue (the Spark→Anchorage 2026-05-19
    $5M reimbursement of the over-sized May 14 payment)."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    # May-shaped flows: interest sweep in, big payment in, $5M back out.
    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 4), date(2026, 3, 14), date(2026, 3, 19)],
        "counterparty": [cp_external,      cp_external,       cp_external],
        "signed_amount":[Decimal("891780"), Decimal("5270830"), Decimal("-5000000")],
    })
    reversals = {cp_external: [(date(2026, 3, 19), Decimal("5000000"))]}

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
        yield_reversal_overrides=reversals,
    )
    # Both inflows pass through as yield (excluded from capital). The
    # registered outflow is ALSO excluded from capital → the capital frame
    # is truly EMPTY (the reversal row gets _capital_amount = 0 and is
    # filtered out, not retained as a negative entry). Revenue =
    # Δvalue − 0 therefore nets all three flows: 891,780 + 5,270,830
    # − 5,000,000 = 1,162,610.
    assert out.empty
    assert out["daily_inflow"].sum() == Decimal("0")


def test_cat_a_unregistered_outflow_to_external_stays_capital(
    config_dir: Path,
):
    """The directional default is preserved: an outflow to an external
    source with NO registered reversal stays capital (a principal
    disbursement to the escrow must never read as negative yield)."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 10)],
        "counterparty": [cp_external],
        "signed_amount":[Decimal("-150000000")],   # principal disbursement
    })
    reversals = {cp_external: [(date(2026, 3, 19), Decimal("5000000"))]}

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
        yield_reversal_overrides=reversals,
    )
    # Unmatched outflow (different date AND amount) → capital, as before.
    assert out["daily_inflow"].sum() == Decimal("-150000000")


def test_cat_a_yield_reversal_misses_on_amount_mismatch(config_dir: Path):
    """Same date but amount off by more than $1 → no match → capital."""
    grove, venue = _grove_e15(config_dir)
    period = _eth_period()
    cp_external = _bytes20("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    src = MockBalanceSource()
    src.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date":   [date(2026, 3, 19)],
        "counterparty": [cp_external],
        "signed_amount":[Decimal("-5000002")],     # $2 off the registered $5M
    })
    reversals = {cp_external: [(date(2026, 3, 19), Decimal("5000000"))]}

    out = _cat_a_capital_inflow_timeseries(
        grove, venue, period,
        balance_source=src,
        external_sources={cp_external},
        yield_reversal_overrides=reversals,
    )
    assert out["daily_inflow"].sum() == Decimal("-5000002")


def test_load_prime_parses_override_blocks_from_spark_yaml(
    config_dir: Path,
):
    """spark.yaml carries an empty ``yield_reversal_overrides`` block (the
    May 2026 Anchorage case turned out to be a round-trip handled via
    ``principal_return_overrides``) — the empty block must parse cleanly,
    and the May 14 round-trip return entry must land on the inflow-side
    mechanism."""
    spark = load_prime(config_dir / "spark.yaml")
    # Empty reversal block parses to no-entries (either {} or no chains).
    assert all(
        not by_addr for by_addr in spark.yield_reversal_overrides.values()
    )
    # The May 14 round-trip return is a principal-return (inflow) override.
    anchorage = _bytes20("0x49506c3aa028693458d6ee816b2ec28522946872")
    eth = spark.principal_return_overrides[Chain.ETHEREUM]
    entries = next(v for k, v in eth.items() if k.value == anchorage)
    assert any(
        e.date == date(2026, 5, 14) and e.amount == Decimal("5270830")
        for e in entries
    )
