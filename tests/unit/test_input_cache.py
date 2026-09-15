"""Finalized snapshots, durable writes, and safe retry behavior."""

from datetime import date

import pytest

from settle.domain.primes import Address, Chain
from settle.extract import hypersync, postgres_store, rpc
from settle.extract.cache import cached
from settle.extract.input_cache import InputCacheError, revenue_input_scope


@pytest.fixture
def storage(tmp_cache_dir, monkeypatch):
    rows = {}
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "100")
    monkeypatch.delenv("SETTLE_INPUT_REVISION", raising=False)
    monkeypatch.delenv("SETTLE_REQUIRE_POSTGRES", raising=False)
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: 1000)
    monkeypatch.setattr(postgres_store, "get", lambda s, k: rows.get((s, k), postgres_store.MISS))
    monkeypatch.setattr(postgres_store, "put", lambda s, k, **kw: rows.setdefault((s, k), kw["payload"]))
    return rows, tmp_cache_dir


@revenue_input_scope
def run(call, *, as_of=date(2026, 8, 15)):
    return call()


def test_legacy_cache_is_not_trusted_and_argument_styles_share_new_key(storage):
    rows, path = storage
    values = iter([10, 20])
    @cached("rpc.test_snapshot")
    def snapshot(chain, block):
        return next(values)
    assert snapshot("ethereum", 500) == 10
    assert run(lambda: snapshot("ethereum", 500)) == 20
    for file in path.glob("*.pkl"):
        file.unlink()
    assert run(lambda: snapshot(block=500, chain="ethereum")) == 20
    assert len(rows) == 2  # legacy + finalized, not a separate keyword-call entry


def test_unfinalized_snapshot_does_not_read_or_populate_cache(storage):
    rows, _ = storage
    @cached("rpc.test_snapshot")
    def snapshot(chain, block):
        pytest.fail("Must reject before RPC")
    with pytest.raises(InputCacheError, match="outside finalized"):
        run(lambda: snapshot("ethereum", 901))
    assert not rows


def test_caught_failure_does_not_persist_a_fallback_zero(storage):
    rows, _ = storage
    attempts = []
    @cached("rpc.test_raw")
    def raw(chain, block):
        attempts.append(1)
        if len(attempts) == 1:
            raise rpc.RPCError("temporarily unavailable")
        return 42
    @cached("rpc.test_outer")
    def outer(chain, block):
        try:
            return raw(chain, block)
        except rpc.RPCError:
            return 0
    assert run(lambda: outer("ethereum", 500)) == 0
    assert not rows
    assert run(lambda: outer("ethereum", 500)) == 42
    assert len(rows) == 2


@pytest.mark.parametrize("reply", [None, "0x1", "0xzz", "invalid", "0x"])
def test_malformed_or_empty_deployed_rpc_response_is_not_cached(storage, monkeypatch, reply):
    rows, _ = storage
    monkeypatch.setenv("ETH_RPC", "http://test.invalid")
    monkeypatch.setattr(rpc, "_post", lambda url, method, args: "0x1234" if method == "eth_getCode" else reply)
    token = Address(bytes(20))
    with pytest.raises(rpc.RPCError):
        run(lambda: rpc.balance_of(Chain.ETHEREUM, token, token, 500))
    assert not any("eth_call" in key[0] or "balance_of" in key[0] for key in rows)


def test_true_zero_and_predeployment_are_cacheable(storage, monkeypatch):
    rows, _ = storage
    monkeypatch.setenv("ETH_RPC", "http://test.invalid")
    monkeypatch.setattr(rpc, "_post", lambda *args: "0x")
    token = Address(bytes(20))
    assert run(lambda: rpc.balance_of(Chain.ETHEREUM, token, token, 500)) == 0
    assert any("balance_of" in key[0] for key in rows)


def test_required_postgres_promotes_local_hit_and_surfaces_write_failure(storage, monkeypatch):
    rows, _ = storage
    @cached("test")
    def snapshot():
        return 42
    assert snapshot() == 42
    rows.clear()  # local file exists, Postgres has not received it
    monkeypatch.setenv("SETTLE_REQUIRE_POSTGRES", "1")
    assert snapshot() == 42
    assert len(rows) == 1
    rows.clear()
    def failed(*args, **kwargs):
        raise postgres_store.PersistenceError("unavailable")
    monkeypatch.setattr(postgres_store, "put", failed)
    with pytest.raises(postgres_store.PersistenceError):
        snapshot()


def test_required_postgres_without_database_fails(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SETTLE_REQUIRE_POSTGRES", "1")
    postgres_store._reset_for_tests()
    try:
        with pytest.raises(postgres_store.PersistenceError):
            postgres_store.get("test", "key")
    finally:
        postgres_store._reset_for_tests()


def test_input_revision_refreshes_inputs_independently_of_code(storage, monkeypatch):
    rows, _ = storage
    @cached("rpc.test_snapshot")
    def snapshot(chain, block):
        return len(rows)
    assert run(lambda: snapshot("ethereum", 500)) == 0
    monkeypatch.setenv("SETTLE_INPUT_REVISION", "corrected-archive")
    assert run(lambda: snapshot("ethereum", 500)) == 1
    assert run(lambda: snapshot("ethereum", 500)) == 1


def test_source_internal_hypersync_lookups_inherit_finalized_policy(storage, monkeypatch):
    calls = []
    monkeypatch.setattr(hypersync, "find_finalized_block_at_or_before", lambda *a: calls.append(a) or 500)
    monkeypatch.setattr(hypersync, "finalized_block_timestamp", lambda *a: calls.append(a) or 1234)
    assert run(lambda: hypersync.find_block_at_or_before("base", 1234)) == 500
    assert run(lambda: hypersync.block_timestamp("base", 500)) == 1234
    assert calls == [("base", 1234, 100), ("base", 500, 100)]


@pytest.mark.parametrize("operation", ["get", "put", "put_many"])
def test_required_database_errors_are_not_silent_cache_misses(monkeypatch, operation):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, *args): raise OSError("connection lost")
        def executemany(self, *args): raise OSError("connection lost")
    class Connection:
        def cursor(self): return Cursor()
    monkeypatch.setenv("SETTLE_REQUIRE_POSTGRES", "1")
    monkeypatch.setattr(postgres_store, "_get_conn", lambda: Connection())
    monkeypatch.setattr(postgres_store, "_drop_conn", lambda reason: None)
    with pytest.raises(postgres_store.PersistenceError):
        if operation == "get":
            postgres_store.get("test", "key")
        elif operation == "put":
            postgres_store.put("test", "key", {}, 42)
        else:
            postgres_store.put_many([("test", "key", {}, 42)])


def test_failed_required_write_does_not_leave_a_local_success(storage, monkeypatch):
    _, path = storage
    monkeypatch.setenv("SETTLE_REQUIRE_POSTGRES", "1")
    def failed(*args, **kwargs):
        raise postgres_store.PersistenceError("connection lost")
    monkeypatch.setattr(postgres_store, "put", failed)
    @cached("test")
    def snapshot():
        return 42
    with pytest.raises(postgres_store.PersistenceError):
        snapshot()
    assert not list(path.glob("*.pkl"))


@pytest.mark.parametrize("kind", ["persistence", "finality", "logs"])
def test_required_failures_cannot_be_swallowed_by_source_fallback(storage, monkeypatch, kind):
    from settle.extract import hypersync_store
    monkeypatch.setenv("SETTLE_REQUIRE_POSTGRES", "1")
    @cached("rpc.test_snapshot")
    def snapshot(chain, block):
        return 0
    def fallback():
        try:
            if kind == "persistence":
                raise postgres_store.PersistenceError("write failed")
            if kind == "logs":
                hypersync_store._add_range(None, "test", 1, 2)
            else:
                snapshot("ethereum", 999)
        except Exception:
            return 0
    expected = InputCacheError if kind == "finality" else postgres_store.PersistenceError
    with pytest.raises(expected):
        run(fallback)


def test_none_capability_fallback_is_not_persisted_as_zero(storage):
    rows, _ = storage
    values = iter([None, 42])
    @cached("rpc.test_optional")
    def optional(chain, block):
        return next(values)
    @cached("rpc.test_outer")
    def outer(chain, block):
        return optional(chain, block) or 0
    assert run(lambda: outer("ethereum", 500)) == 0
    assert not rows
    assert run(lambda: outer("ethereum", 500)) == 42
    assert len(rows) == 2
