"""settle-api — read-only FastAPI over the decoded Postgres store.

    uvicorn settle.api.app:app --host 0.0.0.0 --port $PORT

Endpoints (phase 1):
    GET /healthz                       liveness + DB + latest run
    GET /v1/tmf/history                the sbe_history.json document (schema 1.1.0)
    GET /v1/tmf/kicks                  per-kick rows, newest first (from/to/limit)
    GET /v1/tmf/burns                  per-burn rows + `kind` (from/to/limit)
    GET /v1/tmf/parameter-changes      Splitter/Kicker/Flapper File timeline (limit)
    GET /v1/runs                       run ledger

Conventions: JSON only, ``Cache-Control: public, max-age=300``, ETag on
documents, CORS from ``API_CORS_ORIGINS`` (comma-separated; default '*').
No auth for /v1/tmf/* — the same data is public in settlement-reports.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__ as SETTLE_VERSION
from ..compute.tmf import parse_ts
from ..compute.tmf_history import burn_kind, dec_str, iso_ts
from ..domain.tmf import SbeKick, SbeParamChange, SkyBurn

__all__ = ["Reader", "app", "create_app"]

_REPO = Path(__file__).resolve().parents[3]
_log = logging.getLogger("settle.api")

# Plausible unix-second range for a query bound: 2001-09-09 → 2100-01-01.
# Anything outside it that is still all digits is a year or a YYYYMMDD, which
# would otherwise silently become a 1970 timestamp and match every row.
_MIN_EPOCH, _MAX_EPOCH = 1_000_000_000, 4_102_444_800
_INT_RE = re.compile(r"[+-]?\d+")


class Reader(Protocol):
    """What the API needs from storage — implemented by ``PostgresReader`` and
    by an in-memory double in the tests."""

    def health(self) -> dict[str, Any]: ...
    def history(self) -> dict[str, Any] | None: ...
    def kicks(self, *, from_ts: int | None, to_ts: int | None, limit: int) -> list[SbeKick]: ...
    def burns(self, *, from_ts: int | None, to_ts: int | None, limit: int) -> list[SkyBurn]: ...
    def burn_boundary(self) -> int: ...
    def param_changes(self, *, limit: int) -> list[SbeParamChange]: ...
    def runs(self, *, kind: str | None, limit: int) -> list[dict[str, Any]]: ...
    def close(self) -> None: ...


class PostgresReader:
    def __init__(self, pool: Any, contracts: dict[str, str], notes: list[str],
                 tmf_effective_from: int = 0) -> None:
        self._pool = pool
        self._contracts = contracts
        self._notes = notes
        self._effective_from = tmf_effective_from

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
            return history_document(conn, contracts=self._contracts, notes=self._notes,
                                    tmf_effective_from=self._effective_from)

    def kicks(self, *, from_ts: int | None, to_ts: int | None, limit: int) -> list[SbeKick]:
        from ..store.tmf import load_kicks
        with self._pool.connection() as conn:
            return load_kicks(conn, from_ts=from_ts, to_ts=to_ts, limit=limit)

    def burns(self, *, from_ts: int | None, to_ts: int | None, limit: int) -> list[SkyBurn]:
        from ..store.tmf import load_burns
        with self._pool.connection() as conn:
            return load_burns(conn, from_ts=from_ts, to_ts=to_ts, limit=limit)

    def burn_boundary(self) -> int:
        return self._effective_from

    def param_changes(self, *, limit: int) -> list[SbeParamChange]:
        from ..store.tmf import load_param_changes
        with self._pool.connection() as conn:
            return load_param_changes(conn, limit=limit)

    def runs(self, *, kind: str | None, limit: int) -> list[dict[str, Any]]:
        from ..store.runs import list_runs
        with self._pool.connection() as conn:
            return list_runs(conn, kind=kind, limit=limit)

    def ensure_schema(self) -> None:
        """The API is usually the first thing up on a fresh database; without
        this it would serve ``UndefinedTable`` 500s until the cron's first run."""
        from ..store.db import apply_schema
        with self._pool.connection() as conn:
            apply_schema(conn)

    def close(self) -> None:
        with suppress(Exception):
            self._pool.close()


def _tmf_config() -> tuple[dict[str, str], list[str], int]:
    """``(contracts, notes, tmf_effective_from)`` — the last is the boundary
    every burn is classified against."""
    cfg = yaml.safe_load((_REPO / "config" / "tmf.yaml").read_text())
    contracts = {k: cfg["contracts"][k] for k in ("MCD_SPLIT", "MCD_FLAP", "MCD_KICK", "SKY",
                                                  "MCD_PAUSE_PROXY", "REWARDS_LSSKY_USDS")}
    effective_from = parse_ts((cfg.get("policy") or {}).get("tmf_effective_from")) or 0
    return contracts, list((cfg.get("history") or {}).get("notes") or []), effective_from


def _postgres_reader() -> PostgresReader:
    from psycopg_pool import ConnectionPool

    from ..store.db import configure_session, connect_kwargs, database_url
    pool = ConnectionPool(
        database_url(), min_size=1, max_size=4, open=True,
        kwargs=connect_kwargs(), configure=configure_session,
    )
    contracts, notes, effective_from = _tmf_config()
    return PostgresReader(pool, contracts, notes, effective_from)


def _parse_ts(v: str | None, *, field: str) -> int | None:
    """``from``/``to`` query params: unix seconds or ISO-8601 (``Z`` ok).

    A bare integer outside the plausible epoch range is rejected rather than
    taken literally: ``?from=2026`` and ``?from=20260801`` are a year and a
    date, and reading them as 1970 timestamps would return the whole history
    labelled as a filtered window.
    """
    if v is None or v == "":
        return None
    if _INT_RE.fullmatch(v):
        n = int(v)
        if not _MIN_EPOCH <= n <= _MAX_EPOCH:
            raise HTTPException(
                422,
                f"{field}={v!r} is not a plausible unix timestamp "
                f"({_MIN_EPOCH}-{_MAX_EPOCH}); for a date use ISO-8601, e.g. 2026-08-01T00:00:00Z",
            )
        return n
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, f"bad {field} {v!r}: unix seconds or ISO-8601") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _parse_window(from_: str | None, to: str | None) -> tuple[int | None, int | None]:
    lo, hi = _parse_ts(from_, field="from"), _parse_ts(to, field="to")
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(422, f"from ({lo}) is after to ({hi})")
    return lo, hi


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
    state: dict[str, Reader | None] = {"reader": reader}
    injected = reader is not None
    # The endpoints are sync ``def``s, so Starlette runs them on a threadpool
    # and concurrent cold requests really do interleave here. Without the lock
    # each would build its own ConnectionPool and all but one would be orphaned,
    # holding Postgres sessions for the life of the process.
    lock = threading.Lock()

    def _reader() -> Reader:
        cached = state["reader"]
        if cached is not None:
            return cached
        with lock:
            cached = state["reader"]
            if cached is None:
                cached = _postgres_reader()
                state["reader"] = cached
        return cached

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if not injected:
            # Best effort: a database that is down at boot must not stop the
            # process — /healthz reports it and Railway keeps the container so
            # the failure is readable.
            try:
                r = _reader()
                if isinstance(r, PostgresReader):
                    r.ensure_schema()
            except Exception:
                _log.exception("startup: could not reach the store; serving degraded")
        yield
        built = state["reader"]
        if built is not None and not injected:
            built.close()

    app = FastAPI(title="settle-api", version=SETTLE_VERSION, lifespan=lifespan,
                  description="Read-only API over the MSC settlement pipeline's decoded store.")
    origins = [o.strip() for o in os.environ.get("API_CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"],
                       allow_headers=["*"])

    def get_reader() -> Iterator[Reader]:
        yield _reader()

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        # Deliberately NOT Depends(get_reader): FastAPI resolves dependencies
        # before the body runs, so a missing DATABASE_URL or an unreadable
        # config would escape as a 500 and Railway's health check would restart
        # the container in a loop with no diagnostic. Build the reader here.
        try:
            h = _reader().health()
        except Exception as exc:  # the health endpoint must not 500 on a DB blip
            _log.warning("healthz: %s: %s", type(exc).__name__, exc)
            return {"status": "degraded", "version": SETTLE_VERSION,
                    "db": f"error: {type(exc).__name__}: {exc}"}
        return {"status": "ok", "version": SETTLE_VERSION, **h}

    @app.get("/v1/tmf/history")
    def tmf_history(request: Request, r: Reader = Depends(get_reader)) -> Response:  # noqa: B008
        from ..store.tmf import IncompleteRunError
        try:
            doc = r.history()
        except IncompleteRunError as exc:
            raise HTTPException(503, str(exc)) from exc
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
        lo, hi = _parse_window(from_, to)
        rows = r.kicks(from_ts=lo, to_ts=hi, limit=limit)
        payload = {
            "count": len(rows), "limit": limit, "order": "newest first",
            "kicks": [{
                "ts": iso_ts(k.ts), "block": k.block, "log_index": k.log_index, "tx": k.tx,
                "usds_total": dec_str(k.tot), "usds_buyback": dec_str(k.lot),
                "usds_to_stakers": dec_str(k.pay), "sky_bought": dec_str(k.bought),
                "splitter_burn": dec_str(k.burn), "splitter_hop": k.hop,
                "farm": k.farm, "flapper": k.flapper,
            } for k in reversed(rows)],
        }
        return _document_response(request, payload)

    @app.get("/v1/tmf/burns")
    def tmf_burns(
        request: Request,
        from_: str | None = Query(None, alias="from"),
        to: str | None = None,
        limit: int = Query(1000, ge=1, le=10000),
        r: Reader = Depends(get_reader),  # noqa: B008
    ) -> Response:
        lo, hi = _parse_window(from_, to)
        rows = r.burns(from_ts=lo, to_ts=hi, limit=limit)
        boundary = r.burn_boundary()
        payload = {
            "count": len(rows), "limit": limit, "order": "oldest first",
            # The rule behind `kind`, so a consumer never has to hardcode a date.
            "tmf_effective_from": iso_ts(boundary) if boundary else None,
            "burns": [{
                "ts": iso_ts(b.ts), "block": b.block, "log_index": b.log_index, "tx": b.tx,
                "sender": b.sender, "sink": b.sink, "sky_amount": dec_str(b.amount),
                "protocol": b.protocol, "kind": burn_kind(b, boundary),
            } for b in rows],
        }
        return _document_response(request, payload)

    @app.get("/v1/tmf/parameter-changes")
    def tmf_param_changes(
        request: Request,
        limit: int = Query(1000, ge=1, le=10000),
        r: Reader = Depends(get_reader),  # noqa: B008
    ) -> Response:
        rows = r.param_changes(limit=limit)
        payload = {"count": len(rows), "limit": limit, "order": "oldest first",
                   "parameter_changes": [{
                       "ts": iso_ts(c.ts), "block": c.block, "tx": c.tx, "contract": c.contract,
                       "address": c.address, "what": c.what,
                       "value": dec_str(c.value) if isinstance(c.value, Decimal) else c.value,
                   } for c in rows]}
        return _document_response(request, payload)

    @app.get("/v1/runs")
    def runs(
        request: Request,
        kind: str | None = None,
        # The cron ticks hourly, so a 50-row page would be two days of history.
        limit: int = Query(200, ge=1, le=1000),
        r: Reader = Depends(get_reader),  # noqa: B008
    ) -> Response:
        return _document_response(request, {"runs": r.runs(kind=kind, limit=limit)}, max_age=60)

    return app


app = create_app()
