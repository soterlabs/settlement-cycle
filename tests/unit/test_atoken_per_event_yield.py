"""Unit tests for ``normalize.positions._atoken_per_event_yield``.

Validates that the per-event yield path correctly attributes Aave rebase
revenue across multi-segment drains where the closed-form
``scaled_som × Δindex / RAY`` formula degenerates.

The function reads ``balanceOf`` at strategic blocks (SoM, pre/post for
each event boundary, EoM) and sums rebase yield per segment. Each
boundary is a ``(pre_block, post_block)`` tuple: ``pre_block`` is the
last block where the scaled balance still has its PRE-event value,
``post_block`` is the first block where the scaled balance reflects the
POST-event state. We mock ``balance_at`` with a deterministic dict so
tests don't touch any RPC.
"""

from __future__ import annotations

from settle.normalize.positions import _atoken_per_event_yield


CHAIN = "ethereum"
TOKEN = bytes.fromhex("68215b6533c47ff9f7125ac95adf00fe4a62f79e")
HOLDER = bytes.fromhex("491edfb0b8b608044e227225c715981a30f3a44e")


def _balance_at_factory(by_block: dict[int, int]):
    """Build a ``balance_at(chain, token, holder, block)`` stub that returns
    ``by_block[block]`` and asserts on missing entries (catches off-by-one)."""
    calls: list[int] = []
    def _balance_at(chain, token, holder, block):
        calls.append(block)
        assert block in by_block, f"unexpected balance_at block {block} (recorded={by_block!r})"
        return by_block[block]
    _balance_at.calls = calls
    return _balance_at


def test_no_events_returns_simple_eom_minus_som():
    """With no boundaries, degenerates to ``balanceOf(eom) - balanceOf(som)``.
    Defensive branch; the production caller only routes here on multi-segment
    drains, but the math should still be right."""
    bal = _balance_at_factory({100: 1_000, 200: 1_050})
    out = _atoken_per_event_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        event_boundaries=[], balance_at=bal,
    )
    assert out == 50


def test_single_event_boundary():
    """One event at day boundary (pre=149, post=150). Yield =
    (bal[149] - bal[100]) + (bal[200] - bal[150])."""
    bal = _balance_at_factory({
        100: 10_000,   # SoM
        149: 10_300,   # pre-event (300 yield in first segment)
        150: 4_300,    # post-event (6_000 face-value burn)
        200: 4_350,    # EoM (50 yield in tail)
    })
    out = _atoken_per_event_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        event_boundaries=[(149, 150)], balance_at=bal,
    )
    assert out == 300 + 50  # = 350


def test_multi_segment_drain_three_events():
    """E2-Feb-style scenario: position drained in three steps on distinct
    days. Sum rebase deltas across all four segments."""
    bal = _balance_at_factory({
        100: 15_000_000,        # SoM
        119: 15_010_000,        # pre-ev1 day (10K yield in segment 1)
        120: 10_010_000,        # post-ev1 day (burn 5M face)
        159: 10_020_000,        # pre-ev2 day (10K yield in segment 2)
        160: 4_020_000,         # post-ev2 day (burn 6M face)
        179: 4_025_000,         # pre-ev3 day (5K yield in segment 3)
        180: 1,                 # post-ev3 day (burn ~4M, dust left)
        200: 1,                 # EoM
    })
    out = _atoken_per_event_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        event_boundaries=[(119, 120), (159, 160), (179, 180)],
        balance_at=bal,
    )
    assert out == 10_000 + 10_000 + 5_000 + 0  # = 25,000


def test_consecutive_event_days_zero_segment_between():
    """When events fall on consecutive days, post_block of day N equals
    pre_block of day N+1, so the segment yield between them is zero.
    Documented precision loss. The PRE-day-N segment still captures the
    yield up to that point."""
    bal = _balance_at_factory({
        100: 10_000_000,
        119: 10_005_000,      # pre-day-17
        120: 6_005_000,       # post-day-17 (burn 4M)
        # Day 18: pre_block (119) is same as day 17's post_block? No, would be 120.
        # Properly: pre-day-18 = post-day-17 = block 120.
        121: 100,             # post-day-18 (burn ~6M, dust left)
        200: 100,             # EoM
    })
    out = _atoken_per_event_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        event_boundaries=[(119, 120), (120, 121)],
        balance_at=bal,
    )
    # Segment 1 (SoM → 119): 10_005_000 - 10_000_000 = 5_000
    # Segment 2 (post 120 → pre 120, same block): 6_005_000 - 6_005_000 = 0
    # Tail (post 121 → eom 200): 100 - 100 = 0
    assert out == 5_000


def test_pre_block_below_som_is_clamped_to_som():
    """First event happens on day 1 of period; pre_block (yesterday) lands
    before som_block. Helper clamps to som_block."""
    bal = _balance_at_factory({
        100: 1_000,     # SoM
        110: 500,       # post-event
        200: 510,       # EoM
    })
    out = _atoken_per_event_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        # pre_block 99 is BEFORE period → clamped to som_block=100.
        # So segment 1 reads balance(100) twice → yield = 0.
        event_boundaries=[(99, 110)],
        balance_at=bal,
    )
    assert out == 10  # only the tail yield


def test_post_block_outside_period_is_dropped():
    """A boundary whose post_block lands outside (som, eom] is silently
    dropped — caller can't attribute yield to a non-existent segment."""
    bal = _balance_at_factory({
        100: 1_000,
        149: 1_050,
        150: 500,
        200: 510,
    })
    out = _atoken_per_event_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        # post 50 < som → dropped. post 300 > eom → dropped. Only (149,150).
        event_boundaries=[(45, 50), (149, 150), (250, 300)],
        balance_at=bal,
    )
    assert out == 50 + 10  # only the 149/150 segment + tail


def test_unordered_boundaries_are_sorted():
    """Caller may pass tuples out of order; helper sorts by pre_block."""
    bal = _balance_at_factory({
        100: 1_000,
        119: 1_005, 120: 600,
        159: 605, 160: 100,
        200: 105,
    })
    out_unsorted = _atoken_per_event_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        event_boundaries=[(159, 160), (119, 120)], balance_at=bal,
    )
    out_sorted = _atoken_per_event_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        event_boundaries=[(119, 120), (159, 160)], balance_at=bal,
    )
    assert out_unsorted == out_sorted == 5 + 5 + 5  # = 15


def test_negative_segment_yield_clamps_to_zero():
    """Defensive: if balanceOf decreases within a segment (impossible at
    rest for an accruing token, possible if an event slipped through),
    clamp to 0 rather than report a negative loss."""
    bal = _balance_at_factory({
        100: 1_000,
        149: 900,     # NEGATIVE segment — clamped to 0
        150: 800,     # post-event
        200: 850,     # tail yields +50
    })
    out = _atoken_per_event_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        event_boundaries=[(149, 150)], balance_at=bal,
    )
    assert out == 50


def test_negative_tail_yield_clamps_to_zero():
    """Same clamp applies to the tail segment."""
    bal = _balance_at_factory({
        100: 1_000,
        149: 1_050,
        150: 500,
        200: 400,     # negative tail
    })
    out = _atoken_per_event_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        event_boundaries=[(149, 150)], balance_at=bal,
    )
    assert out == 50
