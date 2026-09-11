"""The ``runs`` table — the versioning spine every fact table points at."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

__all__ = ["config_hash", "fail_run", "finish_run", "latest_run", "list_runs", "start_run"]


def config_hash(cfg: Any) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:16]


def start_run(
    conn: Any, kind: str, *, settle_version: str, prime: str | None = None,
    month: str | None = None, pin_block: int | None = None, pin_ts: int | None = None,
    config_hash: str | None = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO runs (kind, prime, month, pin_block, pin_ts, settle_version, config_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING run_id
            """,
            (kind, prime, month, pin_block,
             datetime.fromtimestamp(pin_ts, UTC) if pin_ts is not None else None,
             settle_version, config_hash),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row[0])


def finish_run(conn: Any, run_id: int, summary: dict[str, Any] | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE runs SET status = 'ok', finished_at = NOW(), summary = %s WHERE run_id = %s",
            (json.dumps(summary or {}, default=str), run_id),
        )
    conn.commit()


def fail_run(conn: Any, run_id: int, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE runs SET status = 'failed', finished_at = NOW(), error = %s WHERE run_id = %s",
            (error[:4000], run_id),
        )
    conn.commit()


_RUN_COLS = ("run_id", "kind", "prime", "month", "pin_block", "pin_ts", "settle_version",
             "config_hash", "started_at", "finished_at", "status", "error", "summary")


def _row(r: tuple[Any, ...]) -> dict[str, Any]:
    d = dict(zip(_RUN_COLS, r, strict=True))
    for k in ("pin_ts", "started_at", "finished_at"):
        if d[k] is not None:
            d[k] = d[k].astimezone(UTC).isoformat().replace("+00:00", "Z")
    return d


def latest_run(conn: Any, kind: str, *, prime: str | None = None, month: str | None = None) -> dict[str, Any] | None:
    """Most recent status='ok' run for the key — the API's default version."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {", ".join(_RUN_COLS)} FROM runs
            WHERE kind = %s AND status = 'ok'
              AND prime IS NOT DISTINCT FROM %s AND month IS NOT DISTINCT FROM %s
            ORDER BY finished_at DESC LIMIT 1
            """,
            (kind, prime, month),
        )
        r = cur.fetchone()
    return _row(r) if r else None


def list_runs(conn: Any, *, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {", ".join(_RUN_COLS)} FROM runs
            WHERE (%s::text IS NULL OR kind = %s)
            ORDER BY started_at DESC LIMIT %s
            """,
            (kind, kind, limit),
        )
        return [_row(r) for r in cur.fetchall()]
