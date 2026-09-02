"""Fee-only isolation of Uniswap V3 ``Collect`` events.

A V3 ``Collect`` withdraws ``tokensOwed``, which mixes two economically
different things: accrued trading fees (revenue) and principal that a
preceding ``DecreaseLiquidity`` moved out of liquidity (capital, already
carried by that event in the inflow timeseries). Crediting the gross
``Collect`` would double-count the principal.

Real numbers from Grove's AUSD/USDC pool (0xbafead7c…), 2026: $72,470,310.97
gross collected, of which only $67,941.11 is fees. Getting this wrong in
either direction is a large, counterparty-visible error, so the pairing is
pinned here.
"""
from __future__ import annotations

import pytest

from settle.domain.primes import Address, Chain
from settle.extract import hypersync as hs
from settle.extract import uniswap_v3 as v3
from settle.extract.hypersync import LogRow, QueryResult

NFPM = Address.from_str("0xc36442b4a4522e871399cd717abdd847ab11fe88")
TID = 1192575


def _log(topic0: str, tx: str, block: int, log_index: int,
         amount0: int, amount1: int, *, token_id: int = TID):
    """Synthesize a HyperSync LogRow. Collect data words are (recipient, a0,
    a1); Increase/Decrease are (liquidity, a0, a1) — same offsets for a0/a1."""
    return LogRow(
        block_number=block,
        log_index=log_index,
        block_time=0,
        address=NFPM.hex,
        topic0=topic0,
        topic1="0x" + f"{token_id:064x}",
        topic2=None,
        topic3=None,
        data="0x" + ("00" * 32) + f"{amount0:064x}" + f"{amount1:064x}",
        transaction_hash=tx,
    )


def _patch(monkeypatch, collects: list, decreases: list) -> None:
    """The reader makes ONE HyperSync call for both topics, so the fake
    returns the merged stream and the accumulator does the ordering."""
    rows = list(collects) + list(decreases)

    def fake(chain, selections, from_block, to_block, **kw):
        return QueryResult(rows=list(rows))
    monkeypatch.setattr(hs, "query_logs", fake)


def test_fee_only_collect_passes_through_in_full(monkeypatch):
    """Grove E12, 2026-08: a harvest with no DecreaseLiquidity is all fees.

    This is the case that was booking as a $49,708.11 loss.
    """
    _patch(monkeypatch,
           [_log(v3.TOPIC_COLLECT, "0x82", 25775310, 4, 31_503_070_000, 30_343_830_000)],
           [])
    out = v3.read_fee_collections(Chain.ETHEREUM, NFPM, TID, 25656000, 25878704)
    assert len(out) == 1
    assert out[0].amount0 == 31_503_070_000
    assert out[0].amount1 == 30_343_830_000
    assert (out[0].amount0 + out[0].amount1) / 1e6 == pytest.approx(61_846.90, abs=0.01)


def test_close_nets_principal_and_keeps_only_the_fee(monkeypatch):
    """Grove E30, 2026-01: Decrease 24,998,012.10 + Collect 25,001,548.90.

    Only the $3,536.81 difference is revenue; crediting the gross Collect
    would invent $25M of yield.
    """
    _patch(monkeypatch,
           [_log(v3.TOPIC_COLLECT, "0xa1", 24228945, 9, 25_001_548_900_000, 0)],
           [_log(v3.TOPIC_DECREASE_LIQUIDITY, "0xa1", 24228945, 7, 24_998_012_100_000, 0)])
    out = v3.read_fee_collections(Chain.ETHEREUM, NFPM, TID, 24136052, 24358292)
    assert len(out) == 1
    assert out[0].amount0 / 1e6 == pytest.approx(3_536.80, abs=0.01)


def test_two_collects_in_one_block_resolve_by_log_index(monkeypatch):
    """A fee-only collect and a close in the same block must not net together.

    The accumulator walks by (block, log_index), so the close's
    DecreaseLiquidity credits `owed` only for the Collect that follows it.
    A block-level netting would let the close's principal erase the
    fee-only collect that preceded it.
    """
    _patch(monkeypatch,
           [_log(v3.TOPIC_COLLECT, "0xaa", 500, 1, 1_000_000, 0),          # fee-only
            _log(v3.TOPIC_COLLECT, "0xbb", 500, 5, 9_000_000, 0)],         # close
           [_log(v3.TOPIC_DECREASE_LIQUIDITY, "0xbb", 500, 4, 9_000_000, 0)])
    out = v3.read_fee_collections(Chain.ETHEREUM, NFPM, TID, 0, 1000)
    assert len(out) == 1, "the fully-principal collect must drop out entirely"
    assert out[0].tx_hash == "0xaa"
    assert out[0].amount0 == 1_000_000


def test_decrease_in_a_later_tx_floors_at_zero_rather_than_going_negative(monkeypatch):
    """A split close (decrease in one tx, collect in another) must not emit a
    negative fee — that would show up as phantom revenue via the outflow sign."""
    _patch(monkeypatch,
           [_log(v3.TOPIC_COLLECT, "0xcc", 600, 2, 5_000_000, 0)],
           [_log(v3.TOPIC_DECREASE_LIQUIDITY, "0xdd", 599, 1, 8_000_000, 0)])
    out = v3.read_fee_collections(Chain.ETHEREUM, NFPM, TID, 0, 1000)
    assert out == [], "unpaired collect < decrease attributes zero fee, not negative"


def test_empty_log_stream_returns_empty(monkeypatch):
    calls: list[tuple] = []

    def fake(chain, selections, from_block, to_block, **kw):
        calls.append((from_block, to_block))
        return QueryResult(rows=[])
    monkeypatch.setattr(hs, "query_logs", fake)
    assert v3.read_fee_collections(Chain.ETHEREUM, NFPM, TID, 0, 100) == []
    assert calls == [(0, 100)], "one merged query for both topics"


def test_decrease_only_stream_yields_no_fee(monkeypatch):
    """A release with no collect in the window is pure capital — already
    carried by the DecreaseLiquidity event in the inflow timeseries."""
    _patch(monkeypatch, [],
           [_log(v3.TOPIC_DECREASE_LIQUIDITY, "0x99", 900, 1, 3_000_000, 0)])
    assert v3.read_fee_collections(Chain.ETHEREUM, NFPM, TID, 0, 1000) == []


def test_inverted_range_returns_empty(monkeypatch):
    monkeypatch.setattr(hs, "query_logs",
                        lambda *a, **k: pytest.fail("must not query"))
    assert v3.read_fee_collections(Chain.ETHEREUM, NFPM, TID, 500, 400) == []


def test_accumulator_spans_partial_collects_of_one_release(monkeypatch):
    """One DecreaseLiquidity drawn down by two later Collects.

    First collect is pure principal; the second exceeds what remains owed,
    so only that excess is fee. Per-transaction pairing would have called
    both collects fee in full.
    """
    _patch(monkeypatch,
           [_log(v3.TOPIC_COLLECT, "0x11", 710, 1, 6_000_000, 0),
            _log(v3.TOPIC_COLLECT, "0x22", 720, 1, 4_500_000, 0)],
           [_log(v3.TOPIC_DECREASE_LIQUIDITY, "0x00", 700, 1, 10_000_000, 0)])
    out = v3.read_fee_collections(Chain.ETHEREUM, NFPM, TID, 0, 1000)
    assert len(out) == 1, "the all-principal first collect must drop out"
    assert out[0].tx_hash == "0x22"
    assert out[0].amount0 == 500_000, "only the excess over remaining owed is fee"


def test_collect_of_boundary_owed_is_reported_in_full(monkeypatch):
    """A Collect with no in-window DecreaseLiquidity is reported in full.

    Whatever was owed at the period boundary sits inside value_som with no
    offsetting event in this period, so its collection must register as an
    outflow. Deliberate consequence of the zero-initialised balance.
    """
    _patch(monkeypatch,
           [_log(v3.TOPIC_COLLECT, "0x33", 800, 1, 7_777_777, 0)], [])
    out = v3.read_fee_collections(Chain.ETHEREUM, NFPM, TID, 0, 1000)
    assert len(out) == 1 and out[0].amount0 == 7_777_777
