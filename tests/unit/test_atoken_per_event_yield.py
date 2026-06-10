"""Unit tests for ``normalize.positions._atoken_per_segment_yield``.

The function applies the closed-form Aave aToken yield formula
``bal_end × scaled_start / scaled_end - bal_start`` per SEGMENT instead
of per-PERIOD. Each consecutive pair of ``segment_blocks`` (plus the
implicit ``som_block``/``eom_block`` anchors) defines one segment.

Key properties under test:

* When scaled is constant within a segment (no events), the formula
  reduces to ``bal_end - bal_start`` (pure rebase).
* When scaled changes within a segment, the formula attributes yield
  on the segment-START scaled basis — accurate to ``V ×
  intraday_index_growth`` per event.
* Clean-exit within a segment (scaled drops to dust) triggers an
  internal binary search for the burn block.

We mock ``balance_at`` and ``scaled_balance_at`` with deterministic
dicts so tests don't touch any RPC.
"""

from __future__ import annotations

from settle.normalize.positions import _atoken_per_segment_yield


CHAIN = "ethereum"
TOKEN = bytes.fromhex("68215b6533c47ff9f7125ac95adf00fe4a62f79e")
HOLDER = bytes.fromhex("491edfb0b8b608044e227225c715981a30f3a44e")


def _factory(by_block_balance: dict[int, int], by_block_scaled: dict[int, int]):
    """Build mock ``balance_at`` and ``scaled_balance_at`` stubs."""
    def _balance_at(chain, token, holder, block):
        assert block in by_block_balance, f"missing balance for block {block}"
        return by_block_balance[block]
    def _scaled_balance_at(chain, token, holder, block):
        assert block in by_block_scaled, f"missing scaled for block {block}"
        return by_block_scaled[block]
    return _balance_at, _scaled_balance_at


def test_no_segments_returns_zero_or_simple_diff():
    """With no segment boundaries (only som and eom), the formula reduces
    to the standard whole-period closed-form. If scaled is constant,
    yield = bal_eom - bal_som."""
    bal = {100: 1_000_000, 200: 1_010_000}
    scaled = {100: 980_000, 200: 980_000}  # constant
    b, s = _factory(bal, scaled)
    out = _atoken_per_segment_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        segment_blocks=[], balance_at=b, scaled_balance_at=s,
    )
    # bal_end × scaled_start / scaled_end - bal_start
    # = 1_010_000 × 980_000 / 980_000 - 1_000_000 = 10_000
    assert out == 10_000


def test_constant_scaled_in_segment_gives_rebase_yield():
    """When scaled doesn't change within a segment, the formula gives
    pure rebase yield (bal_end - bal_start)."""
    bal = {100: 1_000_000, 150: 1_005_000, 200: 1_010_000}
    scaled = {100: 980_000, 150: 980_000, 200: 980_000}
    b, s = _factory(bal, scaled)
    out = _atoken_per_segment_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        segment_blocks=[150], balance_at=b, scaled_balance_at=s,
    )
    # Segment 1 (100→150): 1_005_000 × 980_000 / 980_000 - 1_000_000 = 5_000
    # Segment 2 (150→200): 1_010_000 × 980_000 / 980_000 - 1_005_000 = 5_000
    assert out == 10_000


def test_per_segment_recovers_post_mint_yield():
    """Whole-period closed-form misses yield on newly-minted scaled. The
    per-segment helper recovers most of it with a single boundary at
    the mint day.

    Scenario: $100M position (scaled 99M, index 1.01), gains 1% rebase
    in first half, $50M mint roughly doubles scaled, gains another 1%
    rebase in second half. True yield ≈ $1M + $1.5M = $2.5M.
    """
    bal = {
        100: 100_000_000,
        150: 151_000_000,   # post-mint balance at boundary
        200: 152_500_000,
    }
    scaled = {
        100: 99_009_900,
        150: 148_514_851,   # post-mint scaled
        200: 148_514_851,   # constant in second half
    }
    b, s = _factory(bal, scaled)
    out = _atoken_per_segment_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        segment_blocks=[150], balance_at=b, scaled_balance_at=s,
    )
    # Segment 1 (100→150): bal_end × scaled_start / scaled_end - bal_start
    #   = 151_000_000 × 99_009_900 / 148_514_851 - 100_000_000 ≈ 666K
    # Segment 2 (150→200): scaled constant
    #   = 152_500_000 - 151_000_000 = 1_500_000
    # Total ≈ $2.17M. (The "missing" $330K of yield is intraday — the
    # period from mint-time to EOD on the new scaled balance, which
    # day-resolution boundaries can't separate from the mint itself.
    # For end-of-day-event boundaries on Aave's slow-growing index,
    # this loss is bounded to ~V × half-day-growth ≈ pennies per event.)
    assert out == 2_166_665


def test_per_segment_with_pre_and_post_event_boundaries():
    """When the caller provides BOTH a pre-event and post-event boundary
    (the optimal fixture pattern), per-segment recovers full rebase
    yield across both halves: the mint-day segment carries the entire
    intraday error, isolated from the pre- and post-mint rebases."""
    bal = {
        100: 100_000_000,
        149: 101_000_000,   # pre-mint (1% rebase from som)
        150: 151_000_000,   # post-mint
        200: 152_500_000,   # eom (1% rebase from post-mint)
    }
    scaled = {
        100: 99_009_900,
        149: 99_009_900,    # scaled unchanged until mint
        150: 148_514_851,   # post-mint scaled
        200: 148_514_851,
    }
    b, s = _factory(bal, scaled)
    out = _atoken_per_segment_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        segment_blocks=[149, 150], balance_at=b, scaled_balance_at=s,
    )
    # Segment 1 (100→149): scaled constant → 1_000_000
    # Segment 2 (149→150): scaled jumps; closed-form ≈ -333K → clamped 0
    # Segment 3 (150→200): scaled constant → 1_500_000
    # Total = 2_500_000 (the segment-2 -$333K loss is the intraday
    # mint-event mixing; bounded small in production).
    assert out == 2_500_000


def test_clean_exit_in_segment_uses_binary_search():
    """When scaled drops to dust within a segment (clean exit), the
    helper binary-searches for the burn block and reads balance just
    before. We mock with enough scaled samples to make the search
    converge deterministically."""
    bal = {
        100: 10_000_000,
        149: 10_050_000,   # pre-event (between blocks 100 and 200)
        200: 1,            # post full-exit (dust)
        # Internal binary-search points (the lower half of the segment):
        125: 10_025_000, 113: 10_006_000,
        # The burn happens around block 150; binary search converges to 150.
        150: 1, 138: 10_037_500, 144: 1, 141: 10_043_000, 143: 1, 142: 10_046_000,
        149: 10_050_000,
    }
    scaled = {
        100: 9_800_000,
        125: 9_800_000, 113: 9_800_000, 138: 9_800_000,
        141: 9_800_000, 142: 9_800_000, 143: 1, 144: 1,
        150: 1,
        149: 9_800_000,
        200: 1,
    }
    b, s = _factory(bal, scaled)
    out = _atoken_per_segment_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        segment_blocks=[],   # no event boundaries — single segment over period
        balance_at=b, scaled_balance_at=s,
    )
    # Single segment (100→200), scaled drops to 1 → clean-exit branch.
    # Binary search converges around block 143 (where scaled first
    # crosses dust threshold = scaled_start / 10 = 980_000). Read
    # balance at block 142.
    # yield = bal(142) - bal(100) = 10_046_000 - 10_000_000 = 46_000.
    assert out == 46_000


def test_pre_deployment_segment_returns_zero():
    """If scaled_start is 0 (pre-deployment / empty), the segment can't
    have produced yield. Anything at end is principal injection."""
    bal = {100: 0, 200: 1_000_000}
    scaled = {100: 0, 200: 1_000_000}
    b, s = _factory(bal, scaled)
    out = _atoken_per_segment_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        segment_blocks=[], balance_at=b, scaled_balance_at=s,
    )
    assert out == 0


def test_blocks_outside_period_are_dropped():
    """Boundaries outside [som, eom] are silently dropped."""
    bal = {100: 1_000_000, 150: 1_005_000, 200: 1_010_000}
    scaled = {100: 980_000, 150: 980_000, 200: 980_000}
    b, s = _factory(bal, scaled)
    out = _atoken_per_segment_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        # 50 < som, 300 > eom — both dropped; only 150 is used.
        segment_blocks=[50, 150, 300],
        balance_at=b, scaled_balance_at=s,
    )
    assert out == 10_000


def test_duplicate_consecutive_blocks_collapse():
    """Caller may emit duplicate blocks (events on the same day after
    deduping); they collapse to one boundary."""
    bal = {100: 1_000_000, 150: 1_005_000, 200: 1_010_000}
    scaled = {100: 980_000, 150: 980_000, 200: 980_000}
    b, s = _factory(bal, scaled)
    out = _atoken_per_segment_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        segment_blocks=[150, 150, 150],
        balance_at=b, scaled_balance_at=s,
    )
    assert out == 10_000  # same as single [150]


def test_negative_segment_yield_clamps_to_zero():
    """If the per-segment formula returns a negative value (event
    boundary off-by-one, RPC weirdness), clamp to 0 rather than
    propagate a bogus loss."""
    bal = {100: 1_000_000, 150: 990_000, 200: 1_010_000}
    scaled = {100: 980_000, 150: 980_000, 200: 980_000}
    b, s = _factory(bal, scaled)
    out = _atoken_per_segment_yield(
        CHAIN, TOKEN, HOLDER, som_block=100, eom_block=200,
        segment_blocks=[150], balance_at=b, scaled_balance_at=s,
    )
    # Segment 1: 990K - 1M = -10K → clamped to 0
    # Segment 2: 1.01M - 990K = 20K
    assert out == 20_000


# --- _atoken_index_weighted_inflow per-event rows ---------------------------

def test_per_event_inflow_collapses_same_day_events_to_one_row():
    """Two events on the same calendar date must produce ONE inflow row
    carrying the end-of-day cumulative.

    Regression: E3 April 2026 — Merkl claim (+$1.41M, 15:32) and full
    burn (−$1.41M, 16:13) on Apr 24 produced two rows with the same
    ``block_date``; the consumer's date-max lookup then took the first
    row, dropping the burn and booking a phantom −$1.41M loss."""
    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace

    from settle.domain.primes import Chain
    from settle.normalize.positions import _atoken_index_weighted_inflow

    venue = SimpleNamespace(
        id="E3-test",
        holder_override=None,
        chain=Chain.ETHEREUM,
        token=SimpleNamespace(
            address=SimpleNamespace(value=TOKEN), decimals=0,
        ),
    )
    prime = SimpleNamespace(alm={Chain.ETHEREUM: SimpleNamespace(value=HOLDER)})

    SOM, EOM = 100, 200
    # Day 1 (block 150): claim in +1_000 (mint: scaled jumps).
    # Same day (block 160): full burn to dust.
    balances = {100: 5_000, 149: 5_000, 150: 6_000, 159: 6_000, 160: 1, 200: 1}
    scaleds  = {100: 5_000, 149: 5_000, 150: 6_000, 159: 6_000, 160: 1, 200: 1}
    # _atoken_per_segment_yield binary-searches inside the exit segment.
    for b in range(150, 200):
        balances.setdefault(b, 6_000 if b < 160 else 1)
        scaleds.setdefault(b, 6_000 if b < 160 else 1)
    bal_at, sb_at = _factory(balances, scaleds)

    d = date(2026, 4, 24)
    ts = _atoken_index_weighted_inflow(
        prime, venue, SOM, EOM,
        period_end_date=date(2026, 4, 30),
        balance_at=lambda c, t, h, b: bal_at(c, t, h, b),
        scaled_balance_at=lambda c, t, h, b: sb_at(c, t, h, b),
        transfer_event_blocks=lambda c, t, h, som, eom: [
            (149, 150, d), (159, 160, d),
        ],
    )
    same_day = ts[ts.block_date == d]
    assert len(same_day) == 1, f"expected 1 collapsed row, got\n{ts}"
    # +1_000 in, then −5_999 out → end-of-day cumulative −4_999. (A
    # first-event collapse would read +1_000 here — this pins LAST.)
    cum = same_day["cum_inflow"].iloc[0]
    assert isinstance(cum, Decimal)  # no float coercion through the collapse
    assert cum == Decimal("-4999")
    assert same_day["daily_inflow"].iloc[0] == Decimal("-4999")
