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


def _kick(i: int, bought: str = "3000") -> SbeKick:
    return SbeKick(block=100 + i, log_index=1, ts=T0 + i * 3748, tx=f"0x{i:064x}", tot=D(6000),
                   lot=D(3300), pay=D(2700), bought=D(bought), burn=D("0.55"), hop=3748,
                   farm="0xfarm", flapper="0xflap")


class _MemReader:
    def __init__(self, kicks, burns, params, runs):
        self._k, self._b, self._p, self._r = kicks, burns, params, runs

    def health(self):
        return {"db": "ok", "latest_run": self._r[0] if self._r else None}

    def history(self):
        if not self._r:
            return None
        ds = HistoryDataset(from_block=1, to_block=200, to_ts=T0 + 99_999, kicks=self._k,
                            burns=self._b, param_changes=self._p, contracts={"MCD_SPLIT": "0xs"},
                            notes=["n"])
        doc = build_history_dataset(ds)
        doc["run"] = {"run_id": self._r[0]["run_id"]}
        return doc

    def kicks(self, *, from_ts, to_ts, limit):
        rows = [k for k in self._k if (from_ts is None or k.ts >= from_ts) and (to_ts is None or k.ts <= to_ts)]
        return sorted(rows, key=lambda k: k.block)[-limit:]

    def burns(self, *, from_ts, to_ts):
        return list(self._b)

    def param_changes(self):
        return list(self._p)

    def runs(self, *, kind, limit):
        return [r for r in self._r if kind is None or r["kind"] == kind][:limit]


@pytest.fixture
def client():
    kicks = [_kick(i) for i in range(5)]
    burns = [SkyBurn(block=500, log_index=1, ts=T0 + 86400, tx="0xb", sender="0xpp", sink="0xdead",
                     amount=D("2860943.76"), protocol=True)]
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
    assert doc["totals"]["sky_burn_protocol"] == 2860943.76
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
    assert b["count"] == 1 and b["burns"][0]["protocol"] is True and b["burns"][0]["sky_amount"] == "2860943.76"
    p = client.get("/v1/tmf/parameter-changes").json()
    assert p["parameter_changes"][0] == {
        "ts": "2026-08-17T14:03:10Z", "block": 50, "tx": "0xf", "contract": "MCD_SPLIT",
        "address": "0xsplit", "what": "burn", "value": "0.55",
    }


def test_runs_endpoint_filters_by_kind(client):
    assert client.get("/v1/runs", params={"kind": "tmf_history"}).json()["runs"][0]["run_id"] == 7
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
    assert n == 1 and conn.commits == 1
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
    rows = [(1, 0, T0, "0xt", "MCD_SPLIT", "0xs", "burn", "0.55"),
            (2, 0, T0, "0xt", "MCD_SPLIT", "0xs", "farm", "0x" + "fa" * 20)]
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
