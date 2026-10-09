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
    def fake_query(chain, sel, frm, to, log_fields=None, post=None):
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
        if s.startswith("SELECT covered_from") and "FROM hypersync_ranges" in s:
            self._result = self._s.get("ranges", {}).get(params[0], [])
        elif s.startswith("INSERT INTO hypersync_ranges"):
            stream, lo, hi = params
            self._s.setdefault("ranges", {}).setdefault(stream, []).append((lo, hi))
        elif s.startswith("SELECT covered_from"):
            self._result = self._s["coverage"].get(params[0])
        elif s.startswith("SELECT block_number"):
            stream, lo, hi = params
            self._result = [
                (r.block_number, r.log_index, r.block_time, r.address,
                 r.topic0, r.topic1, r.topic2, r.topic3, r.data, r.transaction_hash)
                for r in sorted(self._s["logs"].get(stream, []),
                                key=lambda r: (r.block_number, r.log_index))
                if lo <= r.block_number <= hi
            ]
        elif s.startswith("INSERT INTO hypersync_coverage"):
            # Mirrors the real SQL: plain overwrite (the caller passes the
            # already-merged honest range; see _set_coverage).
            stream, cf, ct = params
            self._s["coverage"][stream] = (cf, ct)
        # CREATE TABLE / others: no-op

    def executemany(self, sql, seq):
        for p in seq:
            stream = p[0]
            r = LogRow(p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9],
                       transaction_hash=p[10] if len(p) > 10 else None)
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
    def fake_query(chain, sel, frm, to, log_fields=None, post=None):
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


def test_near_head_unfinalized_tail_not_persisted(monkeypatch):
    """A reorg-window fetch persists ONLY the finalized prefix (blocks at or
    below head − margin); the unfinalized tail is served live and never
    written, and coverage never claims past the safe ceiling."""
    conn = _FakeConn()
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: conn)
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "100")
    monkeypatch.setattr(hypersync, "query_logs",
                        lambda *a, **k: QueryResult(rows=[_row(850), _row(950)],
                                                    archive_height=1000))

    sel = [{"address": ["0xtok"], "topics": [["0xt0"]]}]
    # to_block=980 is inside the reorg window (head 1000 − margin 100 = 900):
    # row 850 is finalized → persisted; row 950 is not → live-only.
    rows = hypersync_store.fetch_logs("ethereum", sel, 800, 980)
    assert [r.block_number for r in rows] == [850, 950]
    stream = hypersync_store._stream_key("ethereum", sel)
    assert [r.block_number for r in conn.store["logs"][stream]] == [850]
    assert conn.store["coverage"][stream] == (800, 900)   # clamped to ceiling

    # Re-fetch same range: finalized prefix now served from DB; only the
    # uncovered tail (901..980) needs the network.
    calls = {"n": 0}
    def counting_query(chain, s, frm, to, log_fields=None, post=None):
        calls["n"] += 1
        assert frm == 901                      # incremental: past the ceiling
        return QueryResult(rows=[_row(950)], archive_height=1000)
    monkeypatch.setattr(hypersync, "query_logs", counting_query)
    rows2 = hypersync_store.fetch_logs("ethereum", sel, 800, 980)
    assert [r.block_number for r in rows2] == [850, 950]
    assert calls["n"] == 1


def test_disjoint_backfill_does_not_claim_the_gap(monkeypatch):
    """A backfill DISJOINT from existing coverage must not bridge the two
    islands: LEAST/GREATEST-merging coverage would claim the unfetched gap
    and permanently serve later reads with logs silently missing."""
    conn = _FakeConn()
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: conn)
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "100")

    fetched: list[tuple[int, int]] = []
    def fake_query(chain, sel, frm, to, log_fields=None, post=None):
        fetched.append((frm, to))
        return QueryResult(rows=[_row(frm), _row(to)], archive_height=10_000)
    monkeypatch.setattr(hypersync, "query_logs", fake_query)

    sel = [{"address": ["0xtok"], "topics": [["0xt0"]]}]

    # Recent month first: coverage (1000, 2000).
    hypersync_store.fetch_logs("ethereum", sel, 1000, 2000)
    stream = hypersync_store._stream_key("ethereum", sel)
    assert conn.store["coverage"][stream] == (1000, 2000)

    # Disjoint backfill (100, 200): rows fetched, coverage UNCHANGED —
    # blocks 201..999 were never fetched and must not be claimed.
    hypersync_store.fetch_logs("ethereum", sel, 100, 200)
    assert fetched[-1] == (100, 200)
    assert conn.store["coverage"][stream] == (1000, 2000)

    # A read overlapping the gap must NOT be served from the DB fast path:
    # it re-fetches (a miss), because coverage stayed honest.
    n_before = len(fetched)
    hypersync_store.fetch_logs("ethereum", sel, 150, 1500)
    assert len(fetched) > n_before


def test_adjacent_backfill_extends_coverage_contiguously(monkeypatch):
    """Overlap/adjacency with existing coverage fetches only the missing
    edge range and merges coverage honestly."""
    conn = _FakeConn()
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: conn)
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "100")

    fetched: list[tuple[int, int]] = []
    def fake_query(chain, sel, frm, to, log_fields=None, post=None):
        fetched.append((frm, to))
        return QueryResult(rows=[_row(frm), _row(to)], archive_height=10_000)
    monkeypatch.setattr(hypersync, "query_logs", fake_query)

    sel = [{"address": ["0xtok"], "topics": [["0xt0"]]}]
    hypersync_store.fetch_logs("ethereum", sel, 1000, 2000)

    # Backfill touching the existing range: only (100, 999) is fetched.
    hypersync_store.fetch_logs("ethereum", sel, 100, 1500)
    assert fetched[-1] == (100, 999)
    stream = hypersync_store._stream_key("ethereum", sel)
    assert conn.store["coverage"][stream] == (100, 2000)

    # Now fully covered — no new fetch.
    n = len(fetched)
    hypersync_store.fetch_logs("ethereum", sel, 100, 2000)
    assert len(fetched) == n


# --- transport completeness (query_logs) ------------------------------------

class _PagePost:
    """Replays canned HyperSync response pages."""
    def __init__(self, pages):
        self._pages = list(pages)
    def __call__(self, url, json, headers, timeout):
        import json as _json
        class _R:
            def __init__(self, p): self._p, self.ok, self.status_code, self.text = p, True, 200, _json.dumps(p)
            def json(self): return self._p
        return _R(self._pages.pop(0) if self._pages else {"data": [], "next_block": None})


def test_query_logs_raises_on_archive_below_to_block(monkeypatch):
    """Dune-parity semantics: complete data up to the pin block or FAIL.
    Pagination stalling at the archive head must raise, not return a
    silently truncated range."""
    monkeypatch.setenv("ENVIO_API_TOKEN", "t")
    page = {"data": [], "next_block": 500, "archive_height": 500}
    stall = {"data": [], "next_block": 500, "archive_height": 500}
    with pytest.raises(hypersync.HyperSyncError, match="incomplete"):
        hypersync.query_logs("ethereum", [], 0, 1000, post=_PagePost([page, stall]))


def test_query_logs_raises_on_missing_block_timestamp(monkeypatch):
    """A log with no matching block entry must raise, not be dated 1970
    (which every downstream ``block_date >= start`` filter silently drops)."""
    monkeypatch.setenv("ENVIO_API_TOKEN", "t")
    page = {
        "data": [{"blocks": [], "logs": [{"block_number": 42, "data": "0x"}]}],
        "next_block": 101, "archive_height": 10_000,
    }
    with pytest.raises(hypersync.HyperSyncError, match="no matching block timestamp"):
        hypersync.query_logs("ethereum", [], 0, 100, post=_PagePost([page]))


def test_transaction_hash_round_trips_and_keys_its_own_stream(monkeypatch):
    """A caller asking for transaction_hash gets it back from the DB on a
    re-read, and does NOT share a stream with default-field callers (whose
    persisted rows have no hash)."""
    conn = _FakeConn()
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: conn)
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "100")
    seen_fields = []

    def fake_query(chain, sel, frm, to, log_fields=None, post=None):
        seen_fields.append(log_fields)
        row = LogRow(frm, 0, 1_700_000_000, "0xtok", "0xt0", None, None, None, "0x01",
                     transaction_hash="0xabc" if log_fields else None)
        return QueryResult(rows=[row], archive_height=1000)
    monkeypatch.setattr(hypersync, "query_logs", fake_query)

    sel = [{"address": ["0xtok"]}]
    fields = ["block_number", "log_index", "address", "topic0", "data", "transaction_hash"]
    r1 = hypersync_store.fetch_logs("ethereum", sel, 0, 10, log_fields=fields)
    assert r1[0].transaction_hash == "0xabc" and seen_fields == [fields]
    r2 = hypersync_store.fetch_logs("ethereum", sel, 0, 10, log_fields=fields)   # from DB
    assert r2[0].transaction_hash == "0xabc" and len(seen_fields) == 1
    # default-field caller: separate stream → its own fetch, no hash
    r3 = hypersync_store.fetch_logs("ethereum", sel, 0, 10)
    assert r3[0].transaction_hash is None and len(seen_fields) == 2
    assert hypersync_store._stream_key("ethereum", sel) != hypersync_store._stream_key("ethereum", sel, fields)


def test_schema_is_ensured_once_per_connection(monkeypatch):
    """The ADD COLUMN IF NOT EXISTS takes an ACCESS EXCLUSIVE lock — it must not
    run on every fetch."""
    conn = _FakeConn()
    ddl = {"n": 0}
    real_cursor = conn.cursor

    class _CountingCursor:
        def __init__(self, inner): self._c = inner
        def __enter__(self):
            self._c.__enter__()
            return self
        def __exit__(self, *a): return self._c.__exit__(*a)
        def execute(self, sql, params=()):
            if "CREATE TABLE IF NOT EXISTS hypersync_logs" in sql:
                ddl["n"] += 1
            return self._c.execute(sql, params)
        def executemany(self, *a): return self._c.executemany(*a)
        def fetchone(self): return self._c.fetchone()
        def fetchall(self): return self._c.fetchall()
    conn.cursor = lambda: _CountingCursor(real_cursor())
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: conn)
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "100")
    monkeypatch.setattr(hypersync, "query_logs",
                        lambda *a, **k: QueryResult(rows=[_row(5)], archive_height=1000))
    hypersync_store._SCHEMA_CHECKED.clear()
    for _ in range(3):
        hypersync_store.fetch_logs("ethereum", [{"address": ["0xtok"]}], 0, 10)
    assert ddl["n"] == 1


def test_default_field_set_does_not_fork_the_stream():
    sel = [{"address": ["0xtok"]}]
    k_none = hypersync_store._stream_key("ethereum", sel)
    k_default = hypersync_store._stream_key("ethereum", sel, list(hypersync._DEFAULT_LOG_FIELDS))
    k_tx = hypersync_store._stream_key("ethereum", sel, [*hypersync._DEFAULT_LOG_FIELDS, "transaction_hash"])
    assert k_none == k_default != k_tx


def test_read_covered_logs_requires_the_entire_finalized_range(monkeypatch):
    from settle.extract import hypersync_store as store
    monkeypatch.delenv('HYPERSYNC_NO_STORE', raising=False)
    monkeypatch.setattr(store.postgres_store, '_get_conn', lambda: object())
    monkeypatch.setattr(store, '_ensure_schema_once', lambda c: None)
    monkeypatch.setattr(store, '_coverage_ranges', lambda *a: [(100, 200)])
    reads = []
    def read(*args):
        reads.append(args[-2:])
        return []
    monkeypatch.setattr(store, '_read_rows', read)
    assert store.read_covered_logs('base', [], 90, 150) is None
    assert store.read_covered_logs('base', [], 150, 201) is None
    assert reads == []
    assert store.read_covered_logs('base', [], 110, 180) == []
    assert reads == [(110, 180)]


def test_disjoint_ranges_are_reused_and_only_gap_is_fetched(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: conn)
    calls = []
    def query(chain, sel, lo, hi, **kwargs):
        calls.append((lo, hi))
        return QueryResult(rows=[], archive_height=10000)
    monkeypatch.setattr(hypersync, "query_logs", query)
    for lo, hi in [(100, 200), (400, 500), (400, 500), (100, 500)]:
        assert hypersync_store.fetch_logs("base", [], lo, hi) == []
    assert calls == [(100, 200), (400, 500), (201, 399)]


def test_incomplete_page_without_head_metadata_is_rejected(monkeypatch):
    monkeypatch.setenv("ENVIO_API_TOKEN", "test")
    with pytest.raises(hypersync.HyperSyncError, match="incomplete range"):
        hypersync.query_logs("base", [], 0, 100,
                             post=_PagePost([{"data": [], "next_block": None}]))


def test_pagination_uses_conservative_head_for_persistence(monkeypatch):
    monkeypatch.setenv("ENVIO_API_TOKEN", "test")
    result = hypersync.query_logs("base", [], 0, 100, post=_PagePost([
        {"data": [], "next_block": 50, "archive_height": 550},
        {"data": [], "next_block": 101, "archive_height": 1000},
    ]))
    assert result.archive_height == 550  # block 100 was not finalized on page 1


def test_no_store_conflicts_with_required_persistence(monkeypatch):
    monkeypatch.setenv("SETTLE_REQUIRE_POSTGRES", "1")
    monkeypatch.setenv("HYPERSYNC_NO_STORE", "1")
    with pytest.raises(hypersync_store.postgres_store.PersistenceError):
        hypersync_store.fetch_logs("base", [], 0, 100)


def test_correction_revision_has_independent_event_coverage(monkeypatch):
    original = hypersync_store._stream_key("base", [])
    monkeypatch.setenv("SETTLE_INPUT_REVISION", "corrected")
    assert hypersync_store._stream_key("base", []) != original


def test_legacy_coverage_survives_larger_disjoint_backfill(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: conn)
    stream = hypersync_store._stream_key("base", [])
    conn.store["coverage"][stream] = (0, 100)  # pre-migration, no interval table claims
    calls = []
    def query(chain, sel, lo, hi, **kwargs):
        calls.append((lo, hi))
        return QueryResult(rows=[], archive_height=10000)
    monkeypatch.setattr(hypersync, "query_logs", query)
    hypersync_store.fetch_logs("base", [], 1000, 2000)
    assert conn.store["coverage"][stream] == (1000, 2000)
    assert hypersync_store._coverage_ranges(conn, stream) == [(0, 100), (1000, 2000)]
    hypersync_store.fetch_logs("base", [], 0, 100)
    hypersync_store.fetch_logs("base", [], 0, 2000)
    assert calls == [(1000, 2000), (101, 999)]


def test_checkpoint_restart_retains_completed_chunks(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: conn)
    monkeypatch.setenv("HYPERSYNC_CHECKPOINT_BLOCKS", "10")
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "0")
    calls = []
    fail = True

    def query(chain, sel, lo, hi, **kwargs):
        calls.append((lo, hi))
        if lo == 10 and fail:
            raise ConnectionError("interrupted download")
        return QueryResult(rows=[_row(lo)], archive_height=100)

    monkeypatch.setattr(hypersync, "query_logs", query)
    with pytest.raises(ConnectionError):
        hypersync_store.fetch_logs("ethereum", [], 0, 24)
    assert calls == [(0, 9), (10, 19)]
    calls.clear()
    fail = False
    rows = hypersync_store.fetch_logs("ethereum", [], 0, 24)
    assert calls == [(10, 19), (20, 24)]
    assert [r.block_number for r in rows] == [0, 10, 20]
    calls.clear()
    hypersync_store.fetch_logs("ethereum", [], 0, 24)
    assert calls == []


def test_checkpoint_restart_refetches_unfinalized_tail(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(hypersync_store.postgres_store, "_get_conn", lambda: conn)
    monkeypatch.setenv("HYPERSYNC_CHECKPOINT_BLOCKS", "10")
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "5")
    calls = []

    def query(chain, sel, lo, hi, **kwargs):
        calls.append((lo, hi))
        return QueryResult(rows=[_row(b) for b in range(lo, hi + 1)], archive_height=20)

    monkeypatch.setattr(hypersync, "query_logs", query)
    rows = hypersync_store.fetch_logs("ethereum", [], 0, 20)
    assert len(rows) == 21
    calls.clear()
    rows = hypersync_store.fetch_logs("ethereum", [], 0, 20)
    assert calls == [(16, 20)]
    assert [r.block_number for r in rows] == list(range(21))
