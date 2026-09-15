"""Unit tests for the HyperSync block resolver + client binary search."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from settle.extract import hypersync
from settle.extract.hypersync import HyperSyncBlockUnavailable, HyperSyncError
from settle.normalize.sources.hypersync_block_resolver import HyperSyncBlockResolver


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setenv("SETTLE_NO_CACHE", "1")  # @cached off for deterministic probes


# -- resolver wrapper -----------------------------------------------------

def test_block_at_or_before_passes_unix_ts():
    seen = {}
    def _find(chain, ts):
        seen["call"] = (chain, ts)
        return 42
    r = HyperSyncBlockResolver(find_fn=_find, ts_fn=lambda chain, block: 0)
    anchor = datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
    assert r.block_at_or_before("ethereum", anchor) == 42
    assert seen["call"] == ("ethereum", int(anchor.timestamp()))


def test_naive_datetime_treated_as_utc():
    captured = {}
    r = HyperSyncBlockResolver(find_fn=lambda c, ts: captured.setdefault("ts", ts) or 1,
                               ts_fn=lambda c, b: 0)
    naive = datetime(2026, 6, 1, 0, 0, 0)
    aware = naive.replace(tzinfo=timezone.utc)
    r.block_at_or_before("ethereum", naive)
    assert captured["ts"] == int(aware.timestamp())


def test_block_to_date():
    r = HyperSyncBlockResolver(
        find_fn=lambda c, ts: 0,
        ts_fn=lambda c, b: int(datetime(2026, 6, 15, 12, tzinfo=timezone.utc).timestamp()),
    )
    assert r.block_to_date("ethereum", 123) == date(2026, 6, 15)


# -- client binary search (find_block_at_or_before) -----------------------

def _patch_chain(monkeypatch, head: int, secs_per_block: int = 12):
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: head)
    monkeypatch.setattr(hypersync, "block_timestamp",
                        lambda chain, block: block * secs_per_block)


def test_binary_search_finds_block_at_or_before(monkeypatch):
    _patch_chain(monkeypatch, head=1000, secs_per_block=12)
    # target between blocks 500 (6000s) and 501 (6012s) → highest ≤ target is 500
    assert hypersync.find_block_at_or_before("ethereum", 6005) == 500
    # exact block boundary → that block
    assert hypersync.find_block_at_or_before("ethereum", 6012) == 501
    # target at/after head → head
    assert hypersync.find_block_at_or_before("ethereum", 999999) == 1000
    # target at genesis
    assert hypersync.find_block_at_or_before("ethereum", 0) == 0


def test_binary_search_rejects_pre_genesis(monkeypatch):
    # genesis block 0 has ts 1000; target 500 precedes it
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: 100)
    monkeypatch.setattr(hypersync, "block_timestamp", lambda chain, block: 1000 + block)
    with pytest.raises(HyperSyncError, match="precedes genesis"):
        hypersync.find_block_at_or_before("ethereum", 500)


def test_binary_search_backs_off_unreturnable_head(monkeypatch):
    # archive_height reports 1000, but blocks > 990 aren't query-returnable yet
    # (head-edge race). Search must back off, not raise.
    RETURNABLE = 990
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: 1000)
    def bts(chain, block):
        if block > RETURNABLE:
            raise HyperSyncBlockUnavailable(f"block {block} not returned")
        return block * 12
    monkeypatch.setattr(hypersync, "block_timestamp", bts)
    # historical target still resolves exactly despite the head back-off
    assert hypersync.find_block_at_or_before("ethereum", 6005) == 500
    # target beyond the returnable head → the EXACT newest returnable block
    # (the upward refine finds 990, not the coarse stepped-back 984).
    assert hypersync.find_block_at_or_before("ethereum", 10**9) == RETURNABLE


def test_backoff_refines_to_exact_returnable_head(monkeypatch):
    # Returnable boundary (993) is NOT on a step-back multiple of archive_height
    # (1000) — the coarse step lands on 984; the upward binary-search refine must
    # recover the exact highest returnable block for a near-head target.
    RETURNABLE = 993
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: 1000)
    def bts(chain, block):
        if block > RETURNABLE:
            raise HyperSyncBlockUnavailable(f"block {block} not returned")
        return block * 12
    monkeypatch.setattr(hypersync, "block_timestamp", bts)
    assert hypersync.find_block_at_or_before("ethereum", 10**9) == RETURNABLE
    # a target just below the head still lands on the exact block
    assert hypersync.find_block_at_or_before("ethereum", 992 * 12) == 992


def test_binary_search_matches_reference_algorithm(monkeypatch):
    # irregular block times; compare against a brute-force reference.
    ts_map = {b: b * 13 + (b % 7) for b in range(0, 501)}
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: 500)
    monkeypatch.setattr(hypersync, "block_timestamp", lambda chain, block: ts_map[block])
    for target in (0, 100, 3000, 6500, ts_map[500], ts_map[500] + 1):
        got = hypersync.find_block_at_or_before("ethereum", target)
        expected = max((b for b, t in ts_map.items() if t <= target), default=0)
        assert got == expected, (target, got, expected)


def test_rate_limit_is_not_misinterpreted_as_missing_head(monkeypatch):
    calls = []
    monkeypatch.setattr(hypersync, 'archive_height', lambda chain: 1000)
    def limited(chain, block):
        calls.append(block)
        raise HyperSyncError('HTTP 429')
    monkeypatch.setattr(hypersync, 'block_timestamp', limited)
    with pytest.raises(HyperSyncError, match='429'):
        hypersync.find_block_at_or_before('base', 100)
    assert calls == [1000]


def test_only_complete_resolutions_are_cached(tmp_cache_dir, monkeypatch):
    monkeypatch.setenv("SETTLE_NO_CACHE", "0")
    head = [10]
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: head[0])
    monkeypatch.setattr(hypersync, "block_timestamp", lambda chain, block: block * 12)
    # Both provisional head clamps must stay live as the archive advances.
    assert hypersync.find_block_at_or_before("base", 200) == 10
    head[0] = 12
    assert hypersync.find_block_at_or_before("base", 200) == 12
    head[0] = 30
    assert hypersync.find_block_at_or_before("base", 200) == 16
    def offline(chain):
        raise AssertionError("A completed historical pin must require no network")
    monkeypatch.setattr(hypersync, "archive_height", offline)
    assert hypersync.find_block_at_or_before("base", 200) == 16


def test_regular_chain_resolution_uses_few_timestamp_probes(monkeypatch):
    calls = []
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: 30_000_000)
    def timestamp(chain, block):
        calls.append(block)
        return block * 12
    monkeypatch.setattr(hypersync, "block_timestamp", timestamp)
    assert hypersync.find_block_at_or_before("ethereum", 25_000_000 * 12 + 5) == 25_000_000
    assert len(calls) <= 5


@pytest.mark.parametrize("timestamps", [
    [0] * 400 + list(range(1, 102)),
    list(range(250)) + list(range(1_000_000, 1_000_251)),
    [b * b for b in range(501)],
])
def test_timestamp_guided_search_handles_plateaus_and_long_stalls(monkeypatch, timestamps):
    import bisect
    calls = []
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: len(timestamps) - 1)
    def timestamp(chain, block):
        calls.append(block)
        return timestamps[block]
    monkeypatch.setattr(hypersync, "block_timestamp", timestamp)
    for target in [0, 1, 100, timestamps[-1] - 1]:
        calls.clear()
        assert hypersync.find_block_at_or_before("ethereum", target) == bisect.bisect_right(timestamps, target) - 1
        assert len(calls) <= 4 * len(timestamps).bit_length() + 2
