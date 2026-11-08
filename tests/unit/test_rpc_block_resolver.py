from datetime import UTC, datetime

import pytest

from settle.normalize.sources import rpc_block_resolver as module


def configure(monkeypatch, *, cached, timestamps, fresh):
    monkeypatch.setattr(module.rpc, "find_block_at_or_before", lambda *args: cached)
    monkeypatch.setattr(module.rpc, "block_timestamp", lambda chain, block: timestamps[block])
    calls = []

    def search(*args):
        calls.append(args)
        return fresh

    monkeypatch.setattr(module.rpc, "_find_block_at_or_before_rpc", search)
    return calls


def test_stale_intraday_head_is_replaced(monkeypatch):
    calls = configure(
        monkeypatch, cached=10, timestamps={10: 20, 11: 30, 20: 100, 21: 110}, fresh=20
    )
    assert (
        module.RPCBlockResolver().block_at_or_before("ethereum", datetime.fromtimestamp(105, UTC))
        == 20
    )
    assert len(calls) == 1


def test_exact_boundary_accepts_cache_and_naive_utc(monkeypatch):
    calls = configure(monkeypatch, cached=20, timestamps={20: 100, 21: 110}, fresh=None)
    assert (
        module.RPCBlockResolver().block_at_or_before("ethereum", datetime(1970, 1, 1, 0, 1, 40))
        == 20
    )
    assert not calls


def test_future_candidate_is_replaced(monkeypatch):
    configure(monkeypatch, cached=30, timestamps={30: 120, 20: 100, 21: 110}, fresh=20)
    assert (
        module.RPCBlockResolver().block_at_or_before("ethereum", datetime.fromtimestamp(105, UTC))
        == 20
    )


def test_fresh_head_still_before_cutoff_is_rejected(monkeypatch):
    configure(monkeypatch, cached=10, timestamps={10: 20, 11: 30, 20: 90, 21: 100}, fresh=20)
    with pytest.raises(ValueError, match="do not bracket"):
        module.RPCBlockResolver().block_at_or_before("ethereum", datetime.fromtimestamp(105, UTC))


def test_missing_successor_fails_closed(monkeypatch):
    configure(monkeypatch, cached=10, timestamps={10: 20}, fresh=20)
    with pytest.raises(KeyError):
        module.RPCBlockResolver().block_at_or_before("ethereum", datetime.fromtimestamp(105, UTC))


def test_verified_repair_is_reused_across_venues(monkeypatch):
    calls = configure(
        monkeypatch, cached=10, timestamps={10: 20, 11: 30, 20: 100, 21: 110}, fresh=20
    )
    resolver = module.RPCBlockResolver()
    cutoff = datetime.fromtimestamp(105, UTC)
    assert resolver.block_at_or_before("ethereum", cutoff) == 20
    assert resolver.block_at_or_before("ethereum", cutoff) == 20
    assert len(calls) == 1
