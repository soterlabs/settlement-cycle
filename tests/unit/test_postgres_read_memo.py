"""A capture reuses durable reads without trusting local or mutable values."""

import copy
from decimal import Decimal

import pytest

from settle.extract import postgres_store as store


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.reads = []
        self.error = None

    def cursor(self):
        connection = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def execute(self, query, params):
                if connection.error:
                    raise connection.error
                if query.startswith("SELECT"):
                    connection.reads.append(params)
                    self.row = connection.rows.get(params, store.MISS)
                else:
                    source, key, _, payload = params
                    connection.rows.setdefault((source, key), payload.obj)

            def fetchone(self):
                return None if self.row is store.MISS else (copy.deepcopy(self.row),)

        return Cursor()


@pytest.fixture
def connection(monkeypatch):
    conn = Connection({("rpc.test", "a"): store.encode_payload({"values": [Decimal("1.25")]})})
    monkeypatch.setenv("DATABASE_URL", "postgresql://test.invalid/capture")
    monkeypatch.setenv("SETTLE_REQUIRE_POSTGRES", "1")
    monkeypatch.setattr(store, "_get_conn", lambda: conn)
    monkeypatch.setattr(store, "_drop_conn", lambda reason: None)
    return conn


def test_repeated_read_uses_one_select_and_returns_fresh_mutable_values(connection):
    with store.memoized_reads() as memo:
        first = store.get("rpc.test", "a")
        first["values"].append(999)
        second = store.get("rpc.test", "a")
        second["values"][0] = 0
        assert store.get("rpc.test", "a") == {"values": [Decimal("1.25")]}
        assert len(connection.reads) == 1
        assert memo.hits == 2
        assert memo.misses == 1
        assert memo.stored_bytes > 0
    assert memo.stored_bytes == 0
    store.get("rpc.test", "a")
    assert len(connection.reads) == 2


def test_opt_in_only(connection):
    store.get("rpc.test", "a")
    store.get("rpc.test", "a")
    assert len(connection.reads) == 2


def test_misses_not_cached_and_none_is_a_durable_hit(connection):
    with store.memoized_reads() as memo:
        assert store.get("rpc.test", "new") is store.MISS
        connection.rows["rpc.test", "new"] = None
        assert store.get("rpc.test", "new") is None
        assert store.get("rpc.test", "new") is None
        assert len(connection.reads) == 2
        assert memo.hits == 1


def test_revision_and_argument_keys_are_isolated(connection):
    connection.rows["revision.v2.rpc.test", "a"] = 2
    connection.rows["rpc.test", "b"] = 3
    with store.memoized_reads():
        assert store.get("rpc.test", "a") == {"values": [Decimal("1.25")]}
        assert store.get("revision.v2.rpc.test", "a") == 2
        assert store.get("rpc.test", "b") == 3
        assert store.get("revision.v2.rpc.test", "a") == 2
        assert len(connection.reads) == 3


def test_connection_replacement_invalidates_memo(connection, monkeypatch):
    with store.memoized_reads() as memo:
        store.get("rpc.test", "a")
        other = Connection({("rpc.test", "a"): 77})
        monkeypatch.setattr(store, "_get_conn", lambda: other)
        assert store.get("rpc.test", "a") == 77
        assert len(other.reads) == 1
        assert memo.hits == 0


def test_configured_database_change_requires_new_read(connection, monkeypatch):
    with store.memoized_reads() as memo:
        store.get("rpc.test", "a")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test.invalid/other")
        store.get("rpc.test", "a")
        assert len(connection.reads) == 2
        assert memo.hits == 0


def test_required_unavailable_connection_does_not_use_remembered_data(connection, monkeypatch):
    with store.memoized_reads():
        store.get("rpc.test", "a")
        monkeypatch.setattr(store, "_get_conn", lambda: None)
        with pytest.raises(store.PersistenceError, match="requires an available Postgres"):
            store.get("rpc.test", "a")


def test_read_failure_not_memoized(connection):
    with store.memoized_reads() as memo:
        connection.error = RuntimeError("connection lost")
        with pytest.raises(store.PersistenceError, match="Required Postgres read failed"):
            store.get("rpc.test", "a")
        assert memo.stored_bytes == 0
        connection.error = None
        assert store.get("rpc.test", "a") == {"values": [Decimal("1.25")]}
        assert len(connection.reads) == 1
        assert memo.hits == 0


def test_writes_do_not_promote_unverified_or_conflicting_payloads(connection):
    with store.memoized_reads() as memo:
        store.put("rpc.test", "a", {}, "different from durable row")
        assert memo.stored_bytes == 0
        assert store.get("rpc.test", "a") == {"values": [Decimal("1.25")]}
        assert len(connection.reads) == 1


def test_required_write_failure_still_raises_with_memo_active(connection):
    with store.memoized_reads():
        store.get("rpc.test", "a")
        connection.error = RuntimeError("write unavailable")
        with pytest.raises(store.PersistenceError, match="Required Postgres write failed"):
            store.put("rpc.test", "a", {}, "value")


def test_lru_entry_limit(connection):
    connection.rows.update({("s", key): key for key in "abc"})
    with store.memoized_reads(max_entries=2):
        for key in "abacb":
            assert store.get("s", key) == key
        assert [key for _, key in connection.reads] == list("abcb")


def test_serialized_byte_limit_and_oversized_values(connection):
    connection.rows.update({("s", "a"): "123", ("s", "b"): "456", ("s", "c"): "x" * 50})
    with store.memoized_reads(max_bytes=12) as memo:
        for key in "abaccc":
            store.get("s", key)
            assert memo.stored_bytes <= 12
        assert len(connection.reads) == 6


def test_nested_contexts_do_not_leak_and_exception_restores_outer(connection):
    with store.memoized_reads() as outer:
        store.get("rpc.test", "a")
        with pytest.raises(RuntimeError), store.memoized_reads() as inner:
            store.get("rpc.test", "a")
            raise RuntimeError("abort inner capture")
        assert inner.stored_bytes == 0
        store.get("rpc.test", "a")
        assert len(connection.reads) == 2
        assert outer.hits == 1


@pytest.mark.parametrize("kwargs", [{"max_entries": 0}, {"max_bytes": -1}])
def test_invalid_limits_fail(kwargs):
    with pytest.raises(ValueError, match="must be positive"), store.memoized_reads(**kwargs):
        pass


def test_stats_repr_never_contains_connection_credentials(connection):
    with store.memoized_reads() as memo:
        store.get("rpc.test", "a")
        store.get("rpc.test", "a")
        assert "hits=1" in repr(memo)
        assert "misses=1" in repr(memo)
        assert "postgresql" not in repr(memo)
        assert "capture" not in repr(memo)
