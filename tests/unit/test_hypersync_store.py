"""Unit tests for the reorg-safe HyperSync store (no real DB)."""

from __future__ import annotations

import pytest

from settle.extract import hypersync, hypersync_store
from settle.extract.hypersync import LogRow, QueryResult


def _row(block, li=0):
    return LogRow(block, li, 1_700_000_000 + block, "0xtok", "0xt0", "0xt1", "0xt2", None, "0x01")


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    # Default: no Postgres connection available.
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: None)
    monkeypatch.delenv("HYPERSYNC_NO_STORE", raising=False)


def test_passthrough_without_db(monkeypatch):
    captured = {}
    def fake_query(chain, sel, frm, to, post=None):
        captured.update(chain=chain, frm=frm, to=to)
        return QueryResult(rows=[_row(100), _row(101)], archive_height=200)
    monkeypatch.setattr(hypersync, "query_logs", fake_query)

    rows = hypersync_store.fetch_logs("ethereum", [{"address": ["0xtok"]}], 100, 150)
    assert [r.block_number for r in rows] == [100, 101]
    assert captured == {"chain": "ethereum", "frm": 100, "to": 150}


def test_no_store_env_forces_live(monkeypatch):
    # Even if a DB were present, HYPERSYNC_NO_STORE=1 bypasses it.
    monkeypatch.setenv("HYPERSYNC_NO_STORE", "1")
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn",
                        lambda: (_ for _ in ()).throw(AssertionError("DB must not be touched")))
    monkeypatch.setattr(hypersync, "query_logs",
                        lambda *a, **k: QueryResult(rows=[_row(5)], archive_height=10))
    rows = hypersync_store.fetch_logs("ethereum", [{"address": ["0xtok"]}], 0, 9)
    assert [r.block_number for r in rows] == [5]


def test_stream_key_stable_and_selection_sensitive():
    k1 = hypersync_store._stream_key("ethereum", [{"address": ["0xa"], "topics": [["0xt"]]}])
    k2 = hypersync_store._stream_key("ethereum", [{"address": ["0xa"], "topics": [["0xt"]]}])
    k3 = hypersync_store._stream_key("ethereum", [{"address": ["0xb"], "topics": [["0xt"]]}])
    assert k1 == k2 and k1 != k3
    assert len(k1) == 64  # sha256 hex


# --- reorg-safe persistence path, with a fake in-memory Postgres connection ---

class _FakeCursor:
    def __init__(self, store):
        self._s = store
        self._result = None

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT covered_from"):
            self._result = self._s["coverage"].get(params[0])
        elif s.startswith("SELECT block_number"):
            stream, lo, hi = params
            self._result = [
                (r.block_number, r.log_index, r.block_time, r.address,
                 r.topic0, r.topic1, r.topic2, r.topic3, r.data)
                for r in sorted(self._s["logs"].get(stream, []),
                                key=lambda r: (r.block_number, r.log_index))
                if lo <= r.block_number <= hi
            ]
        elif s.startswith("INSERT INTO hypersync_coverage"):
            stream, cf, ct = params
            cur = self._s["coverage"].get(stream)
            self._s["coverage"][stream] = (
                (min(cur[0], cf), max(cur[1], ct)) if cur else (cf, ct)
            )
        # CREATE TABLE / others: no-op

    def executemany(self, sql, seq):
        for p in seq:
            stream = p[0]
            r = LogRow(p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9])
            self._s["logs"].setdefault(stream, []).append(r)

    def fetchone(self): return self._result
    def fetchall(self): return self._result or []


class _FakeConn:
    def __init__(self): self.store = {"coverage": {}, "logs": {}}
    def cursor(self): return _FakeCursor(self.store)


def test_persists_only_finalized_and_serves_incrementally(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: conn)
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "100")

    calls = {"n": 0}
    def fake_query(chain, sel, frm, to, post=None):
        calls["n"] += 1
        # head=1000; return one row per requested boundary block for visibility
        rows = [_row(frm), _row(min(to, 900))]
        return QueryResult(rows=rows, archive_height=1000)
    monkeypatch.setattr(hypersync, "query_logs", fake_query)

    sel = [{"address": ["0xtok"], "topics": [["0xt0"]]}]

    # First historical fetch [0, 500] (well below head-margin=900): persists, 1 query.
    r1 = hypersync_store.fetch_logs("ethereum", sel, 0, 500)
    assert calls["n"] == 1
    assert [r.block_number for r in r1] == [0, 500]

    # Re-fetch same range → served from DB, no new query.
    r2 = hypersync_store.fetch_logs("ethereum", sel, 0, 500)
    assert calls["n"] == 1
    assert [r.block_number for r in r2] == [0, 500]

    # Extend to [0, 800] → only the incremental tail (501..800) is fetched.
    r3 = hypersync_store.fetch_logs("ethereum", sel, 0, 800)
    assert calls["n"] == 2
    assert 800 in [r.block_number for r in r3] and 0 in [r.block_number for r in r3]


def test_near_head_not_persisted(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: conn)
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "100")
    monkeypatch.setattr(hypersync, "query_logs",
                        lambda *a, **k: QueryResult(rows=[_row(950)], archive_height=1000))

    sel = [{"address": ["0xtok"], "topics": [["0xt0"]]}]
    # to_block=980 is within reorg window (head 1000 - margin 100 = 900) → serve live, store nothing.
    rows = hypersync_store.fetch_logs("ethereum", sel, 900, 980)
    assert [r.block_number for r in rows] == [950]
    assert conn.store["coverage"] == {}   # nothing persisted
    assert conn.store["logs"] == {}
