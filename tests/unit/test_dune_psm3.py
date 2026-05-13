"""Unit tests for `settle.normalize.sources.dune_psm3` helpers.

Covers the two pure functions used by ``DunePsm3Source`` to convert Dune
result rows into the block-indexed cumulative-value structure the rest of
the source bisects against. The class methods themselves (preload /
shares_of / convert_to_asset_value / pool_reserve_at) are integration-style
— they're exercised end-to-end through the orchestrator and not unit-
tested here. These helpers are pure functions whose edge cases are easy
to enumerate.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from settle.normalize.sources.dune_psm3 import (
    _bisect_cum_at_or_before,
    _df_to_block_cum,
)


# ---------------------------------------------------------------------------
# _bisect_cum_at_or_before — used by every shares_of / total_shares lookup
# ---------------------------------------------------------------------------

def test_bisect_empty_history_returns_zero():
    """No deposit/withdraw events ever → balance is 0 at every block.
    Matches on-chain ``shares(holder) == 0`` for an address that never
    interacted with the PSM3."""
    assert _bisect_cum_at_or_before([], 1_000_000) == 0


def test_bisect_block_before_first_event_returns_zero():
    """Querying a block earlier than the holder's first deposit returns
    zero — the holder hadn't opened a position yet."""
    history = [(24_200_000, 1_000_000_000), (24_300_000, 500_000_000)]
    assert _bisect_cum_at_or_before(history, 24_199_999) == 0


def test_bisect_exact_block_match_returns_that_events_cumulative():
    """Block N is the event block itself → return the cumulative AFTER the
    event (i.e. including it). This matches the on-chain semantic:
    ``shares(holder)`` read at block N reflects events from block N's
    transactions."""
    history = [(24_200_000, 1_000_000_000), (24_300_000, 1_500_000_000)]
    assert _bisect_cum_at_or_before(history, 24_200_000) == 1_000_000_000
    assert _bisect_cum_at_or_before(history, 24_300_000) == 1_500_000_000


def test_bisect_block_between_events_returns_prior_cumulative():
    """Between two event blocks → the earlier event's cumulative."""
    history = [(24_200_000, 1_000_000_000), (24_300_000, 1_500_000_000)]
    assert _bisect_cum_at_or_before(history, 24_250_000) == 1_000_000_000


def test_bisect_block_after_all_events_returns_last_cumulative():
    """Block past the last event → the most recent cumulative."""
    history = [(24_200_000, 1_000_000_000), (24_300_000, 1_500_000_000)]
    assert _bisect_cum_at_or_before(history, 99_999_999) == 1_500_000_000


def test_bisect_handles_withdrawal_back_to_zero():
    """A full withdrawal reduces cumulative to 0 — should return 0 (not
    None or -1) for any block at or after the withdrawal."""
    history = [
        (24_200_000, 1_000_000_000),
        (24_250_000,           0),  # full withdrawal
        (24_300_000,   500_000),    # later partial re-deposit
    ]
    assert _bisect_cum_at_or_before(history, 24_250_000) == 0
    assert _bisect_cum_at_or_before(history, 24_275_000) == 0
    assert _bisect_cum_at_or_before(history, 24_300_000) == 500_000


# ---------------------------------------------------------------------------
# _df_to_block_cum — Dune DataFrame → list[(block, cum)] adapter
# ---------------------------------------------------------------------------

def test_df_to_block_cum_empty_dataframe():
    """An empty Dune result (e.g. a holder that never touched the PSM3 in
    the window) should produce an empty history list, NOT raise."""
    df = pd.DataFrame(columns=["block_number", "cum_shares"])
    assert _df_to_block_cum(df, value_col="cum_shares") == []


def test_df_to_block_cum_none_dataframe():
    """Guard against a None result (e.g. from a swallowed exception path)."""
    assert _df_to_block_cum(None, value_col="cum_shares") == []


def test_df_to_block_cum_preserves_order():
    """Dune returns ORDER BY block_number, evt_index but defensively re-
    sorts. Out-of-order input should still produce a sorted list."""
    df = pd.DataFrame({
        "block_number": [24_300_000, 24_100_000, 24_200_000],
        "cum_shares":   [1_500_000_000, 100_000_000, 1_000_000_000],
    })
    out = _df_to_block_cum(df, value_col="cum_shares")
    assert out == [
        (24_100_000, 100_000_000),
        (24_200_000, 1_000_000_000),
        (24_300_000, 1_500_000_000),
    ]


def test_df_to_block_cum_coerces_decimal_to_int():
    """Dune-returned numeric columns are Python ``Decimal``; downstream
    callers expect plain ``int`` so on-chain comparisons (shares × …)
    don't accidentally widen to Decimal arithmetic."""
    df = pd.DataFrame({
        "block_number": [24_200_000],
        "cum_shares":   [Decimal("1000000000")],
    })
    out = _df_to_block_cum(df, value_col="cum_shares")
    assert out == [(24_200_000, 1_000_000_000)]
    assert isinstance(out[0][1], int)


def test_df_to_block_cum_coerces_string_decimal_to_int():
    """Defensive: pickled DataFrames sometimes lose the Decimal dtype and
    re-load as strings. The helper coerces ``str → int`` to keep callers
    type-stable."""
    df = pd.DataFrame({
        "block_number": [24_200_000],
        "cum_shares":   ["1000000000"],
    })
    out = _df_to_block_cum(df, value_col="cum_shares")
    assert out == [(24_200_000, 1_000_000_000)]


def test_df_to_block_cum_named_value_column():
    """``value_col`` is parameterised because pool / holder queries use
    different column names (``cum_total_shares`` vs ``cum_shares``)."""
    df = pd.DataFrame({
        "block_number":     [24_200_000, 24_300_000],
        "cum_total_shares": [5_000_000_000, 7_500_000_000],
    })
    out = _df_to_block_cum(df, value_col="cum_total_shares")
    assert out == [(24_200_000, 5_000_000_000), (24_300_000, 7_500_000_000)]


def test_bisect_round_trip_with_df_to_block_cum():
    """Smoke-check the two helpers compose correctly — a Dune-shape
    DataFrame round-trips into the bisect-friendly structure and the
    bisect returns the right cum at a between-events block."""
    df = pd.DataFrame({
        "block_number": [24_100_000, 24_200_000, 24_300_000],
        "cum_shares":   [Decimal("1e9"), Decimal("2e9"), Decimal("1.5e9")],
    })
    history = _df_to_block_cum(df, value_col="cum_shares")
    assert _bisect_cum_at_or_before(history, 24_150_000) == 10**9
    assert _bisect_cum_at_or_before(history, 24_250_000) == 2 * 10**9
    assert _bisect_cum_at_or_before(history, 24_350_000) == int(1.5 * 10**9)
