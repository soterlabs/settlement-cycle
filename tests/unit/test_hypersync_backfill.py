import pytest

from scripts.backfill_hypersync_balances import backfill, merge_coverage
from settle.extract import hypersync_store as store
from settle.extract.hypersync import LogRow
from settle.normalize.sources.hypersync_balances import _addr_topic, _touching_selections


@pytest.mark.parametrize("finalized_end", [190, 200])
def test_parent_coverage_and_token_filtering_guard_child_materialization(monkeypatch, finalized_end):
    owner = bytes([1]) * 20
    tokens = [bytes([i]) * 20 for i in [2, 3, 4]]
    coverage, writes = {}, {}
    monkeypatch.setattr(store.postgres_store, "_get_conn", lambda: object())
    monkeypatch.setattr(store, "_ensure_schema_once", lambda c: None)
    monkeypatch.setattr(store, "_get_coverage", lambda c, key: coverage.get(key))
    monkeypatch.setattr(store, "_set_coverage", lambda c, key, lo, hi: coverage.update({key: (lo, hi)}))
    monkeypatch.setattr(store, "_persist", lambda c, key, rows: writes.update({key: rows}))
    rows = [LogRow(120 + i, 0, 100, "0x" + token.hex(), None, _addr_topic(owner),
                   _addr_topic(bytes(20)), None, "0x01") for i, token in enumerate(tokens[:2])]
    def fetch(chain, selections, start, end):
        coverage[store._stream_key(chain, selections)] = (start, finalized_end)
        return rows
    monkeypatch.setattr(store, "fetch_logs", fetch)
    if finalized_end < 200:
        with pytest.raises(RuntimeError, match="not fully finalized"):
            backfill("base", tokens, owner, 100, 200)
        assert not writes
        return
    backfill("base", tokens, owner, 100, 200)
    for i, token in enumerate(tokens):
        key = store._stream_key("base", _touching_selections(token, owner))
        assert writes[key] == ([rows[i]] if i < 2 else [])
        assert coverage[key] == (100, 200)


def test_child_coverage_never_bridges_unfetched_gaps():
    assert merge_coverage((10, 20), (30, 40)) == (10, 20)
    assert merge_coverage((10, 20), (21, 40)) == (10, 40)
    assert merge_coverage((10, 20), (0, 40)) == (0, 40)
