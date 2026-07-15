"""Unit tests for the HyperSync block resolver + client binary search."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from settle.extract import hypersync
from settle.extract.hypersync import HyperSyncError
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
            raise HyperSyncError(f"block {block} not returned")
        return block * 12
    monkeypatch.setattr(hypersync, "block_timestamp", bts)
    # historical target still resolves exactly despite the head back-off
    assert hypersync.find_block_at_or_before("ethereum", 6005) == 500
    # target beyond the returnable head → newest returnable block, no crash
    r = hypersync.find_block_at_or_before("ethereum", 10**9)
    assert 0 < r <= RETURNABLE


def test_binary_search_matches_reference_algorithm(monkeypatch):
    # irregular block times; compare against a brute-force reference.
    ts_map = {b: b * 13 + (b % 7) for b in range(0, 501)}
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: 500)
    monkeypatch.setattr(hypersync, "block_timestamp", lambda chain, block: ts_map[block])
    for target in (0, 100, 3000, 6500, ts_map[500], ts_map[500] + 1):
        got = hypersync.find_block_at_or_before("ethereum", target)
        expected = max((b for b, t in ts_map.items() if t <= target), default=0)
        assert got == expected, (target, got, expected)
