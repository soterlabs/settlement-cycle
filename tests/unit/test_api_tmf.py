"""Unit tests for settle-api (in-memory Reader double) and the store's row
mapping (fake cursor). No Postgres."""

from __future__ import annotations

import json
from decimal import Decimal as D
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="settle-api tests need the `api` extra (pip install -e '.[api,dev]')")
from fastapi.testclient import TestClient

from settle.api.app import create_app
from settle.compute.tmf_history import SCHEMA_VERSION, HistoryDataset, build_history_dataset
from settle.domain.tmf import SbeKick, SbeParamChange, SkyBurn
from settle.store import runs as R
from settle.store import tmf as S

T0 = 1_786_975_400
CAST = 1_786_975_343   # policy.tmf_effective_from — the burn-kind boundary


def _kick(i: int, bought: str = "3000") -> SbeKick:
    return SbeKick(block=100 + i, log_index=1, ts=T0 + i * 3748, tx=f"0x{i:064x}", tot=D(6000),
                   lot=D(3300), pay=D(2700), bought=D(bought), burn=D("0.55"), hop=3748,
                   farm="0xfarm", flapper="0xflap")


class _MemReader:
    def __init__(self, kicks, burns, params, runs):
        self._k, self._b, self._p, self._r = kicks, burns, params, runs
        self.closed = False

    def health(self):
        return {"db": "ok", "latest_run": self._r[0] if self._r else None}

    def history(self):
        if not self._r:
            return None
        ds = HistoryDataset(tmf_effective_from=CAST, from_block=1, to_block=200,
                            to_ts=T0 + 99_999, kicks=self._k, burns=self._b,
                            param_changes=self._p, contracts={"MCD_SPLIT": "0xs"}, notes=["n"])
        doc = build_history_dataset(ds, generated_at="2026-09-11T00:03:28Z")
        doc["run"] = {"run_id": self._r[0]["run_id"]}
        return doc

    def kicks(self, *, from_ts, to_ts, limit):
        rows = [k for k in self._k if (from_ts is None or k.ts >= from_ts) and (to_ts is None or k.ts <= to_ts)]
        return sorted(rows, key=lambda k: k.block)[-limit:]

    def burns(self, *, from_ts, to_ts, limit):
        return list(self._b)[-limit:]        # latest N, oldest-first — as the store does

    def burn_boundary(self):
        return CAST

    def param_changes(self, *, limit):
        return list(self._p)[-limit:]

    def close(self):
        self.closed = True

    def runs(self, *, kind, limit):
        return [r for r in self._r if kind is None or r["kind"] == kind][:limit]


@pytest.fixture
def client():
    kicks = [_kick(i) for i in range(5)]
    burns = [
        # the 2025 supply correction (before the cast) and a 2026 engine burn
        SkyBurn(block=400, log_index=1, ts=CAST - 86400, tx="0xc", sender="0xpp", sink="0xdead",
                amount=D("426292860.23"), protocol=True),
        SkyBurn(block=500, log_index=1, ts=T0 + 86400, tx="0xb", sender="0xpp", sink="0xdead",
                amount=D("2860943.76"), protocol=True),
    ]
    params = [SbeParamChange(block=50, log_index=0, ts=T0 - 10, tx="0xf", contract="MCD_SPLIT",
                             what="burn", value=D("0.55"), address="0xsplit")]
    runs = [{"run_id": 7, "kind": "tmf_history", "status": "ok", "pin_block": 200}]
    return TestClient(create_app(_MemReader(kicks, burns, params, runs)))


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"] == "ok" and body["latest_run"]["run_id"] == 7


def test_history_document_is_the_dataset_contract_with_etag(client):
    r = client.get("/v1/tmf/history")
    assert r.status_code == 200
    doc = r.json()
    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["totals"]["kicks"] == 5 and doc["totals"]["usds_total"] == 30000.0
    # both protocol kinds are in the fixture; the legacy field sums them
    assert doc["totals"]["sky_burn_engine"] == 2860943.76           # after the cast
    assert doc["totals"]["sky_burn_supply_correction"] == 426292860.23   # before it
    assert doc["totals"]["sky_burn_protocol"] == 429153803.99
    assert doc["schema_version"] == "1.2.0"
    assert doc["run"]["run_id"] == 7
    assert r.headers["Cache-Control"] == "public, max-age=300"
    etag = r.headers["ETag"]
    assert client.get("/v1/tmf/history", headers={"If-None-Match": etag}).status_code == 304


def test_history_503_before_first_run():
    c = TestClient(create_app(_MemReader([], [], [], [])))
    assert c.get("/v1/tmf/history").status_code == 503


def test_kicks_window_limit_and_exact_decimals(client):
    r = client.get("/v1/tmf/kicks", params={"from": str(T0 + 3748), "limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2 and body["order"] == "newest first"
    assert [k["block"] for k in body["kicks"]] == [104, 103]
    assert body["kicks"][0]["usds_buyback"] == "3300" and body["kicks"][0]["splitter_burn"] == "0.55"
    # ISO timestamps accepted too
    r2 = client.get("/v1/tmf/kicks", params={"to": "2026-08-17T14:10:00Z"})
    assert r2.status_code == 200 and r2.json()["count"] == 1
    assert client.get("/v1/tmf/kicks", params={"from": "yesterday"}).status_code == 422
    assert client.get("/v1/tmf/kicks", params={"limit": 0}).status_code == 422


def test_burns_and_parameter_changes(client):
    b = client.get("/v1/tmf/burns").json()
    assert b["count"] == 2 and all(x["protocol"] for x in b["burns"])
    assert [x["sky_amount"] for x in b["burns"]] == ["426292860.23", "2860943.76"]
    p = client.get("/v1/tmf/parameter-changes").json()
    assert p["parameter_changes"][0] == {
        "ts": "2026-08-17T14:03:10Z", "block": 50, "tx": "0xf", "contract": "MCD_SPLIT",
        "address": "0xsplit", "what": "burn", "value": "0.55",
    }


def test_runs_endpoint_filters_by_kind(client):
    body = client.get("/v1/runs", params={"kind": "tmf_history"}).json()
    assert body["runs"][0]["run_id"] == 7 and body["count"] == 1
    assert client.get("/v1/runs", params={"kind": "other"}).json()["runs"] == []


def test_healthz_degrades_instead_of_500():
    class _Broken(_MemReader):
        def health(self):
            raise ConnectionError("db down")
    c = TestClient(create_app(_Broken([], [], [], [])))
    r = c.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "degraded"


# ── store: SQL parameter mapping through a recording cursor ─────────────────

class _Cur:
    def __init__(self, log, rows=None):
        self.log, self.rows, self.rowcount = log, rows or [], 0

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=()):
        self.log.append((" ".join(sql.split()), params))

    def executemany(self, sql, seq, returning=False):
        seq = list(seq)
        self.log.append((" ".join(sql.split()), seq))
        self.rowcount = len(seq)

    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


class _Conn:
    def __init__(self, rows=None):
        self.log: list[Any] = []
        self._rows = rows
        self.commits = 0

    def cursor(self): return _Cur(self.log, self._rows)
    def commit(self): self.commits += 1


def test_upsert_kicks_maps_columns_and_is_on_conflict_do_nothing():
    conn = _Conn()
    n = S.upsert_kicks(conn, [_kick(0, "123.456")], run_id=9)
    sql, rows = conn.log[0]
    assert "ON CONFLICT (block_number, log_index) DO NOTHING" in sql
    assert rows[0][:4] == (100, 1, T0, "0x" + "0" * 64) and rows[0][7] == D("123.456") and rows[0][-1] == 9
    # The caller commits once for all three upserts + finish_run, so a run that
    # dies midway leaves nothing durable.
    assert n == 1 and conn.commits == 0
    assert S.upsert_kicks(conn, [], run_id=9) == 0


def test_load_kicks_round_trips_rows_oldest_first():
    rows = [(101, 1, T0 + 1, "0xb", D(6000), D(3300), D(2700), D("10.5"), D("0.55"), 3748, "0xf", "0xl"),
            (100, 1, T0, "0xa", D(6000), D(3300), D(2700), D("11.5"), D("0.55"), 3748, "0xf", "0xl")]
    ks = S.load_kicks(_Conn(rows), limit=2)
    assert [k.block for k in ks] == [100, 101] and ks[1].bought == D("10.5")
    conn = _Conn(rows)
    S.load_kicks(conn, from_ts=1, to_ts=2, limit=5)
    sql, params = conn.log[0]
    assert "block_time >= %s AND block_time <= %s" in sql and params == [1, 2] and "LIMIT 5" in sql


def test_load_param_changes_restores_decimal_or_address():
    # rows as Postgres returns them for this query: block DESC, reversed to
    # oldest-first by the loader
    rows = [(2, 0, T0, "0xt", "MCD_SPLIT", "0xs", "farm", "0x" + "fa" * 20),
            (1, 0, T0, "0xt", "MCD_SPLIT", "0xs", "burn", "0.55")]
    a, b = S.load_param_changes(_Conn(rows))
    assert a.value == D("0.55") and isinstance(a.value, D)
    assert b.value == "0x" + "fa" * 20 and b.address == "0xs"


def test_run_lifecycle_sql():
    conn = _Conn(rows=[(42,)])
    rid = R.start_run(conn, "tmf_history", settle_version="0.1.0+abc", config_hash="h")
    assert rid == 42 and "INSERT INTO runs" in conn.log[0][0]
    R.finish_run(conn, rid, {"kicks_total": 5})
    sql, params = conn.log[1]
    assert "status = 'ok'" in sql and json.loads(params[0]) == {"kicks_total": 5} and params[1] == 42
    R.fail_run(conn, rid, "boom")
    assert "status = 'failed'" in conn.log[2][0]
    assert len(R.config_hash({"a": 1})) == 16 and R.config_hash({"a": 1}) == R.config_hash({"a": 1})


# ── review round 4 ──────────────────────────────────────────────────────────

def test_history_etag_is_stable_across_seconds(client):
    """`generated_at` comes from the run, not from `now` — otherwise the ETag
    rotates every second and no conditional GET ever hits."""
    first = client.get("/v1/tmf/history")
    second = client.get("/v1/tmf/history")
    assert first.content == second.content
    assert first.headers["ETag"] == second.headers["ETag"]
    assert first.json()["generated_at"] == "2026-09-11T00:03:28Z"


def test_incomplete_run_is_503_not_a_guessed_range():
    class _Incomplete(_MemReader):
        def history(self):
            from settle.store.tmf import IncompleteRunError
            raise IncompleteRunError("run 9 is marked ok but lacks pin_block")
    c = TestClient(create_app(_Incomplete([], [], [], [{"run_id": 9}])))
    r = c.get("/v1/tmf/history")
    assert r.status_code == 503 and "pin_block" in r.json()["detail"]


@pytest.mark.parametrize("bad", ["2026", "20260801", "0", "99999999999"])
def test_bare_integers_outside_the_epoch_range_are_rejected(client, bad):
    r = client.get("/v1/tmf/kicks", params={"from": bad})
    assert r.status_code == 422
    assert "plausible unix timestamp" in r.json()["detail"]


def test_window_bounds_must_be_ordered(client):
    r = client.get("/v1/tmf/kicks", params={"from": T0 + 10_000, "to": T0})
    assert r.status_code == 422 and "is after" in r.json()["detail"]


def test_every_collection_endpoint_states_which_end_limit_keeps(client):
    """`order` describes how the returned rows are arranged, not which ones
    survived the cut. On /v1/tmf/burns those differ — oldest-first over the
    LATEST N — which reads as "the oldest N" unless truncation is explicit."""
    for path in ("/v1/tmf/kicks", "/v1/tmf/burns", "/v1/tmf/parameter-changes", "/v1/runs"):
        body = client.get(path, params={"limit": 2}).json()
        assert body["truncation"] == "the latest N in the window, by block", path
        assert body["order"] in ("newest first", "oldest first"), path

    # and the claim is true: limit=1 on burns keeps the NEWEST row
    one = client.get("/v1/tmf/burns", params={"limit": 1}).json()
    every = client.get("/v1/tmf/burns").json()
    assert one["count"] == 1
    assert one["burns"][0]["ts"] == every["burns"][-1]["ts"]   # the latest, not the earliest


def test_burns_carry_their_kind_and_the_boundary_rule(client):
    """A consumer must be able to tell the engine's own burns from the one-off
    supply correction without hardcoding a date — both are Pause Proxy -> zero
    address on-chain."""
    body = client.get("/v1/tmf/burns").json()
    assert body["tmf_effective_from"] == "2026-08-17T14:02:23Z"
    # both are protocol burns to the same sink; only the cast tells them apart
    assert [x["kind"] for x in body["burns"]] == ["supply_correction", "engine"]
    assert all(x["protocol"] for x in body["burns"])


def test_burns_and_param_changes_are_bounded(client):
    for path in ("/v1/tmf/burns", "/v1/tmf/parameter-changes"):
        assert client.get(path).json()["limit"] == 1000
        assert client.get(path, params={"limit": 10001}).status_code == 422
    assert client.get("/v1/tmf/burns", params={"limit": 1}).json()["count"] == 1


def test_healthz_degrades_when_the_reader_cannot_be_built(monkeypatch):
    """FastAPI resolves dependencies before the endpoint body, so /healthz must
    not take the reader as a dependency — a missing DATABASE_URL would
    otherwise 500 and Railway would restart-loop the container."""
    from settle.api import app as app_mod
    monkeypatch.setattr(app_mod, "_postgres_reader",
                        lambda: (_ for _ in ()).throw(RuntimeError("DATABASE_URL is not set")))
    r = TestClient(create_app()).get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded" and "DATABASE_URL is not set" in body["db"]


def test_reader_is_built_once_under_concurrent_cold_requests(monkeypatch):
    """Without the lock each concurrent cold request builds its own
    ConnectionPool and all but one are orphaned."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    from settle.api import app as app_mod
    built = []

    def slow_factory():
        time.sleep(0.05)
        r = _MemReader([], [], [], [{"run_id": 1, "kind": "tmf_history"}])
        built.append(r)
        return r

    monkeypatch.setattr(app_mod, "_postgres_reader", slow_factory)
    c = TestClient(create_app())
    with ThreadPoolExecutor(max_workers=4) as ex:
        codes = [f.result().status_code for f in [ex.submit(c.get, "/healthz") for _ in range(4)]]
    assert codes == [200] * 4
    assert len(built) == 1


def test_injected_reader_is_not_closed_by_the_lifespan():
    """A caller-supplied reader (tests, embedding) is not ours to close."""
    r = _MemReader([], [], [], [])
    with TestClient(create_app(r)):
        pass
    assert r.closed is False


# ── store: block-bounded reads and the strict document ──────────────────────

def test_loaders_bound_rows_by_pin_block():
    conn = _Conn([])
    S.load_kicks(conn, to_block=200, limit=5)
    sql, params = conn.log[0]
    assert "block_number <= %s" in sql and params == [200]
    conn = _Conn([])
    S.load_burns(conn, to_block=200, limit=7)
    sql, params = conn.log[0]
    assert "block_number <= %s" in sql and params == [200] and "LIMIT 7" in sql
    assert "ORDER BY block_number DESC" in sql       # last N, then reversed to oldest-first
    conn = _Conn([])
    S.load_param_changes(conn, to_block=200)
    assert "block_number <= %s" in conn.log[0][0]


def test_history_document_refuses_a_run_without_its_range(monkeypatch):
    monkeypatch.setattr(S, "latest_run", lambda conn, kind: {
        "run_id": 9, "pin_block": None, "summary": {}, "finished_at": "t", "settle_version": "v"})
    with pytest.raises(S.IncompleteRunError, match="pin_block"):
        S.history_document(_Conn([]), contracts={}, notes=[], tmf_effective_from=CAST)
    monkeypatch.setattr(S, "latest_run", lambda conn, kind: {
        "run_id": 9, "pin_block": 200, "summary": {"from_block": 1}, "finished_at": "t",
        "settle_version": "v"})
    with pytest.raises(S.IncompleteRunError, match="to_ts"):
        S.history_document(_Conn([]), contracts={}, notes=[], tmf_effective_from=CAST)


def test_history_document_stamps_the_run_and_bounds_by_its_pin(monkeypatch):
    monkeypatch.setattr(S, "latest_run", lambda conn, kind: {
        "run_id": 7, "pin_block": 200, "finished_at": "2026-09-11T00:03:28Z",
        "settle_version": "0.1.0+abc",
        "summary": {"from_block": 1, "to_ts": T0 + 99_999}})
    conn = _Conn([])
    doc = S.history_document(conn, contracts={"MCD_SPLIT": "0xs"}, notes=["n"],
                             tmf_effective_from=CAST)
    assert doc["source"]["from_block"] == 1 and doc["source"]["to_block"] == 200
    assert doc["generated_at"] == "2026-09-11T00:03:28Z"   # stable per run
    assert doc["run"]["settle_version"] == "0.1.0+abc"
    assert all("block_number <= %s" in sql for sql, _ in conn.log)
