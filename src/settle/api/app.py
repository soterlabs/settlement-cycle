"""settle-api — read-only FastAPI over the decoded Postgres store.

    uvicorn settle.api.app:app --host 0.0.0.0 --port $PORT

Endpoints (phase 1):
    GET /healthz                       liveness + DB + latest run
    GET /v1/tmf/history                the sbe_history.json document (schema 1.1.0)
    GET /v1/tmf/kicks                  per-kick rows, newest first (from/to/limit)
    GET /v1/tmf/burns                  per-burn rows
    GET /v1/tmf/parameter-changes      Splitter/Kicker/Flapper File timeline
    GET /v1/runs                       run ledger

Conventions: JSON only, ``Cache-Control: public, max-age=300``, ETag on
documents, CORS from ``API_CORS_ORIGINS`` (comma-separated; default '*').
No auth for /v1/tmf/* — the same data is public in settlement-reports.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__ as SETTLE_VERSION
from ..compute.tmf_history import _dec
from ..domain.tmf import SbeKick, SbeParamChange, SkyBurn

__all__ = ["Reader", "app", "create_app"]

_REPO = Path(__file__).resolve().parents[3]


def _TS(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), UTC).isoformat().replace("+00:00", "Z")


class Reader(Protocol):
    """What the API needs from storage — implemented by ``PostgresReader`` and
    by an in-memory double in the tests."""

    def health(self) -> dict[str, Any]: ...
    def history(self) -> dict[str, Any] | None: ...
    def kicks(self, *, from_ts: int | None, to_ts: int | None, limit: int) -> list[SbeKick]: ...
    def burns(self, *, from_ts: int | None, to_ts: int | None) -> list[SkyBurn]: ...
    def param_changes(self) -> list[SbeParamChange]: ...
    def runs(self, *, kind: str | None, limit: int) -> list[dict[str, Any]]: ...


class PostgresReader:
    def __init__(self, pool: Any, contracts: dict[str, str], notes: list[str]) -> None:
        self._pool = pool
        self._contracts = contracts
        self._notes = notes

    def health(self) -> dict[str, Any]:
        from ..store.runs import latest_run
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            run = latest_run(conn, "tmf_history")
        return {"db": "ok", "latest_run": run}

    def history(self) -> dict[str, Any] | None:
        from ..store.tmf import history_document
        with self._pool.connection() as conn:
            return history_document(conn, contracts=self._contracts, notes=self._notes)

    def kicks(self, *, from_ts: int | None, to_ts: int | None, limit: int) -> list[SbeKick]:
        from ..store.tmf import load_kicks
        with self._pool.connection() as conn:
            return load_kicks(conn, from_ts=from_ts, to_ts=to_ts, limit=limit)

    def burns(self, *, from_ts: int | None, to_ts: int | None) -> list[SkyBurn]:
        from ..store.tmf import load_burns
        with self._pool.connection() as conn:
            return load_burns(conn, from_ts=from_ts, to_ts=to_ts)

    def param_changes(self) -> list[SbeParamChange]:
        from ..store.tmf import load_param_changes
        with self._pool.connection() as conn:
            return load_param_changes(conn)

    def runs(self, *, kind: str | None, limit: int) -> list[dict[str, Any]]:
        from ..store.runs import list_runs
        with self._pool.connection() as conn:
            return list_runs(conn, kind=kind, limit=limit)


def _tmf_config() -> tuple[dict[str, str], list[str]]:
    cfg = yaml.safe_load((_REPO / "config" / "tmf.yaml").read_text())
    contracts = {k: cfg["contracts"][k] for k in ("MCD_SPLIT", "MCD_FLAP", "MCD_KICK", "SKY",
                                                  "MCD_PAUSE_PROXY", "REWARDS_LSSKY_USDS")}
    return contracts, list((cfg.get("history") or {}).get("notes") or [])


def _postgres_reader() -> PostgresReader:
    from psycopg_pool import ConnectionPool

    from ..store.db import database_url
    pool = ConnectionPool(database_url(), min_size=1, max_size=4, open=True,
                          kwargs={"connect_timeout": 15})
    contracts, notes = _tmf_config()
    return PostgresReader(pool, contracts, notes)


def _parse_ts(v: str | None) -> int | None:
    """``from``/``to`` query params: unix seconds or ISO-8601 (``Z`` ok)."""
    if v is None or v == "":
        return None
    if v.isdigit():
        return int(v)
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, f"bad timestamp {v!r}: unix seconds or ISO-8601") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _num(x: Decimal) -> str:
    return _dec(x)


def _etag_json(payload: Any) -> tuple[bytes, str]:
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    return body, '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def _document_response(request: Request, payload: Any, max_age: int = 300) -> Response:
    body, etag = _etag_json(payload)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(content=body, media_type="application/json",
                    headers={"ETag": etag, "Cache-Control": f"public, max-age={max_age}"})


def create_app(reader: Reader | None = None) -> FastAPI:
    app = FastAPI(title="settle-api", version=SETTLE_VERSION,
                  description="Read-only API over the MSC settlement pipeline's decoded store.")
    origins = [o.strip() for o in os.environ.get("API_CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"],
                       allow_headers=["*"])
    state: dict[str, Reader | None] = {"reader": reader}

    def get_reader() -> Iterator[Reader]:
        r = state["reader"]
        if r is None:
            r = _postgres_reader()
            state["reader"] = r
        yield r

    @app.get("/healthz")
    def healthz(r: Reader = Depends(get_reader)) -> dict[str, Any]:  # noqa: B008
        try:
            h = r.health()
        except Exception as exc:  # the health endpoint must not 500 on a DB blip
            return {"status": "degraded", "db": f"error: {type(exc).__name__}", "version": SETTLE_VERSION}
        return {"status": "ok", "version": SETTLE_VERSION, **h}

    @app.get("/v1/tmf/history")
    def tmf_history(request: Request, r: Reader = Depends(get_reader)) -> Response:  # noqa: B008
        doc = r.history()
        if doc is None:
            raise HTTPException(503, "no successful tmf_history run yet")
        return _document_response(request, doc)

    @app.get("/v1/tmf/kicks")
    def tmf_kicks(
        request: Request,
        from_: str | None = Query(None, alias="from"),
        to: str | None = None,
        limit: int = Query(500, ge=1, le=5000),
        r: Reader = Depends(get_reader),  # noqa: B008
    ) -> Response:
        rows = r.kicks(from_ts=_parse_ts(from_), to_ts=_parse_ts(to), limit=limit)
        payload = {
            "count": len(rows), "limit": limit, "order": "newest first",
            "kicks": [{
                "ts": _TS(k.ts), "block": k.block, "log_index": k.log_index, "tx": k.tx,
                "usds_total": _num(k.tot), "usds_buyback": _num(k.lot),
                "usds_to_stakers": _num(k.pay), "sky_bought": _num(k.bought),
                "splitter_burn": _num(k.burn), "splitter_hop": k.hop,
                "farm": k.farm, "flapper": k.flapper,
            } for k in reversed(rows)],
        }
        return _document_response(request, payload)

    @app.get("/v1/tmf/burns")
    def tmf_burns(
        request: Request,
        from_: str | None = Query(None, alias="from"),
        to: str | None = None,
        r: Reader = Depends(get_reader),  # noqa: B008
    ) -> Response:
        rows = r.burns(from_ts=_parse_ts(from_), to_ts=_parse_ts(to))
        payload = {"count": len(rows), "burns": [{
            "ts": _TS(b.ts), "block": b.block, "log_index": b.log_index, "tx": b.tx,
            "sender": b.sender, "sink": b.sink, "sky_amount": _num(b.amount), "protocol": b.protocol,
        } for b in rows]}
        return _document_response(request, payload)

    @app.get("/v1/tmf/parameter-changes")
    def tmf_param_changes(request: Request, r: Reader = Depends(get_reader)) -> Response:  # noqa: B008
        rows = r.param_changes()
        payload = {"count": len(rows), "parameter_changes": [{
            "ts": _TS(c.ts), "block": c.block, "tx": c.tx, "contract": c.contract,
            "address": c.address, "what": c.what,
            "value": _num(c.value) if isinstance(c.value, Decimal) else c.value,
        } for c in rows]}
        return _document_response(request, payload)

    @app.get("/v1/runs")
    def runs(
        request: Request,
        kind: str | None = None,
        limit: int = Query(50, ge=1, le=500),
        r: Reader = Depends(get_reader),  # noqa: B008
    ) -> Response:
        return _document_response(request, {"runs": r.runs(kind=kind, limit=limit)}, max_age=60)

    return app


app = create_app()
