"""Reorg-safe persistence for HyperSync log queries.

Sits between the HyperSync client (``hypersync.py``) and the domain sources. It
turns "re-query everything every run" into "fetch only new finalized blocks,
serve the rest from Postgres" — WITHOUT ever caching stale/reorg-prone data.

Design (see db/schema.sql):
  * A **stream** = one HyperSync log selection (chain + addresses + topics),
    hashed to a stable id. Rows are stored per ``(stream, block, log_index)``.
  * **Staleness guard:** rows are persisted only for blocks at or below
    ``chain_head - HYPERSYNC_REORG_MARGIN`` (finalized, cannot reorg). If a
    query's upper bound is inside the reorg window, it's served **live and not
    written**. Because block-pinned facts are immutable, a stored row is never
    stale — so a re-run at the same/earlier pin reads straight from Postgres,
    and a later pin fetches only the incremental block range.
  * **Graceful degradation:** with ``DATABASE_URL`` unset (or psycopg missing),
    every call is a live pass-through — identical behaviour to no store at all.

Apply the schema once: ``psql "$DATABASE_URL" -f db/schema.sql`` (the store also
self-bootstraps the tables on first use).
"""

from __future__ import annotations

import hashlib
import json
import os
import weakref
from collections.abc import Callable
from functools import wraps
from typing import Any

import requests

from . import hypersync, postgres_store

_DEFAULT_REORG_MARGIN = 500


def _database_operation(fn):
    """Make required DB failures fatal even when a source has a fallback."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if postgres_store.required():
                raise postgres_store.PersistenceError(f"HyperSync cache {fn.__name__} failed") from exc
            raise
    return wrapped


def _reorg_margin() -> int:
    margin = int(os.environ.get("HYPERSYNC_REORG_MARGIN", str(_DEFAULT_REORG_MARGIN)))
    if margin < 0:
        raise ValueError("HYPERSYNC_REORG_MARGIN must be nonnegative")
    return margin


def _stream_key(
    chain: str, selections: list[dict[str, Any]], log_fields: list[str] | None = None
) -> str:
    """Stable id for one selection. The field set is part of the key ONLY when
    the caller asks for non-default fields (e.g. ``transaction_hash``): rows
    persisted under the default set have NULL there, and serving them to a
    caller that needs the hash would silently break its joins."""
    key: dict[str, Any] = {"chain": chain, "sel": selections}
    from .input_cache import input_revision
    if input_revision() != "0":
        key["input_revision"] = input_revision()
    if log_fields is not None and sorted(log_fields) != sorted(hypersync._DEFAULT_LOG_FIELDS):
        key["fields"] = sorted(log_fields)
    blob = json.dumps(key, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def fetch_logs(
    chain: str,
    selections: list[dict[str, Any]],
    from_block: int,
    to_block: int,
    *,
    log_fields: list[str] | None = None,
    post: Callable[..., Any] = requests.post,
) -> list[hypersync.LogRow]:
    """Return all logs matching ``selections`` in ``[from_block, to_block]``.

    Reads finalized rows from Postgres when covered; fetches only the missing
    (incremental or first-time) range from HyperSync; never persists rows inside
    the reorg window. ``log_fields`` is forwarded to the client (add
    ``"transaction_hash"`` to get it back on every row, persisted included).
    """
    if from_block > to_block:
        return []

    def live(lo: int, hi: int) -> hypersync.QueryResult:
        return hypersync.query_logs(chain, selections, lo, hi, log_fields=log_fields, post=post)

    if os.environ.get("HYPERSYNC_NO_STORE") == "1":
        if postgres_store.required():
            raise postgres_store.PersistenceError("HYPERSYNC_NO_STORE conflicts with SETTLE_REQUIRE_POSTGRES")
        return live(from_block, to_block).rows

    conn = postgres_store._get_conn()
    if conn is None:  # no DB → live pass-through (same as before the store existed)
        postgres_store._unavailable()
        return live(from_block, to_block).rows

    stream = _stream_key(chain, selections, log_fields)
    _ensure_schema_once(conn)
    ranges = _coverage_ranges(conn, stream)
    missing = _missing_ranges(from_block, to_block, ranges)
    if not missing:
        return _read_rows(conn, stream, from_block, to_block)

    live_rows: list[hypersync.LogRow] = []
    for lo, hi in missing:
        res = live(lo, hi)
        live_rows.extend(res.rows)
        safe = res.archive_height - _reorg_margin() if res.archive_height else -1
        end = min(hi, safe)
        if lo <= end:
            finalized = [r for r in res.rows if lo <= r.block_number <= end]
            # Rows precede coverage. A crash can leave unclaimed rows, never
            # a range claiming rows which failed to persist. Each missing
            # interval is independent, so retries retain completed intervals.
            _persist(conn, stream, finalized)
            _add_range(conn, stream, lo, end)
            ranges = _merge_ranges([*ranges, (lo, end)])
            # Keep the legacy single-range view honest for older workers.
            # All intervals remain in the new append-only coverage table.
            largest = max(ranges, key=lambda r: r[1] - r[0])
            _set_coverage(conn, stream, *largest)
    return _merge(_read_rows(conn, stream, from_block, to_block), live_rows,
                  from_block, to_block)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged


def _missing_ranges(lo: int, hi: int, ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    missing = []
    cursor = lo
    for start, end in ranges:
        if start > cursor:
            missing.append((cursor, min(hi, start - 1)))
        cursor = max(cursor, end + 1)
        if cursor > hi:
            break
    if cursor <= hi:
        missing.append((cursor, hi))
    return [(start, end) for start, end in missing if start <= end]


@_database_operation
def _coverage_ranges(conn: Any, stream: str) -> list[tuple[int, int]]:
    legacy = _get_coverage(conn, stream)
    with conn.cursor() as cur:
        cur.execute("SELECT covered_from, covered_to FROM hypersync_ranges WHERE stream = %s", (stream,))
        ranges = [(int(row[0]), int(row[1])) for row in cur.fetchall()]
    return _merge_ranges(ranges + ([legacy] if legacy else []))


@_database_operation
def _add_range(conn: Any, stream: str, lo: int, hi: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO hypersync_ranges (stream, covered_from, covered_to) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING", (stream, lo, hi),
        )


def read_covered_logs(chain: str, selections: list[dict[str, Any]],
                      from_block: int, to_block: int) -> list[hypersync.LogRow] | None:
    """Read a fully covered finalized selection, or None without a live fetch.

    Enables a narrower consumer to reuse a known-complete superset. Coverage
    is mandatory: stored rows alone cannot prove that a range has no gaps.
    """
    if os.environ.get("HYPERSYNC_NO_STORE") == "1":
        return None
    conn = postgres_store._get_conn()
    if conn is None:
        return None
    _ensure_schema_once(conn)
    stream = _stream_key(chain, selections)
    if _missing_ranges(from_block, to_block, _coverage_ranges(conn, stream)):
        return None
    return _read_rows(conn, stream, from_block, to_block)


# --------------------------------------------------------------------------
# Postgres helpers (thin; reuse postgres_store's connection + graceful state).
# --------------------------------------------------------------------------

# Connections whose schema has been checked this process. ``_ensure_schema``
# includes an ``ALTER TABLE … ADD COLUMN IF NOT EXISTS``, which takes an
# ACCESS EXCLUSIVE lock on the shared log table even when the column already
# exists — running it on every fetch (hundreds per run) blocks behind any
# concurrent reader and can hit statement_timeout. Once per connection is
# enough: the schema cannot change underneath a live connection.
_SCHEMA_CHECKED: weakref.WeakSet[Any] = weakref.WeakSet()


def _ensure_schema_once(conn: Any) -> None:
    if conn in _SCHEMA_CHECKED:
        return
    _ensure_schema(conn)
    _SCHEMA_CHECKED.add(conn)


@_database_operation
def _ensure_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS hypersync_logs (
                stream TEXT NOT NULL, block_number BIGINT NOT NULL,
                log_index INTEGER NOT NULL, block_time BIGINT NOT NULL,
                address TEXT NOT NULL, topic0 TEXT, topic1 TEXT, topic2 TEXT,
                topic3 TEXT, data TEXT NOT NULL,
                PRIMARY KEY (stream, block_number, log_index)
            );
            ALTER TABLE hypersync_logs ADD COLUMN IF NOT EXISTS transaction_hash TEXT;
            CREATE INDEX IF NOT EXISTS idx_hypersync_logs_stream_block
                ON hypersync_logs (stream, block_number);
            CREATE TABLE IF NOT EXISTS hypersync_ranges (
                stream TEXT NOT NULL, covered_from BIGINT NOT NULL, covered_to BIGINT NOT NULL,
                PRIMARY KEY (stream, covered_from, covered_to),
                CHECK (covered_from <= covered_to)
            );
            CREATE TABLE IF NOT EXISTS hypersync_coverage (
                stream TEXT PRIMARY KEY, covered_from BIGINT NOT NULL,
                covered_to BIGINT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )


@_database_operation
def _get_coverage(conn: Any, stream: str) -> tuple[int, int] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT covered_from, covered_to FROM hypersync_coverage WHERE stream = %s",
            (stream,),
        )
        row = cur.fetchone()
    return (int(row[0]), int(row[1])) if row else None


@_database_operation
def _set_coverage(conn: Any, stream: str, cfrom: int, cto: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO hypersync_coverage (stream, covered_from, covered_to)
            VALUES (%s, %s, %s)
            ON CONFLICT (stream) DO UPDATE SET
                -- Plain overwrite: the caller passes the already-merged honest
                -- range. A LEAST/GREATEST merge here would silently bridge
                -- disjoint fetches, claiming an unfetched gap (see fetch_logs).
                covered_from = EXCLUDED.covered_from,
                covered_to   = EXCLUDED.covered_to,
                updated_at   = NOW()
            """,
            (stream, cfrom, cto),
        )


@_database_operation
def _persist(conn: Any, stream: str, rows: list[hypersync.LogRow]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO hypersync_logs
                (stream, block_number, log_index, block_time, address,
                 topic0, topic1, topic2, topic3, data, transaction_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (stream, block_number, log_index) DO NOTHING
            """,
            [
                (stream, r.block_number, r.log_index, r.block_time, r.address,
                 r.topic0, r.topic1, r.topic2, r.topic3, r.data, r.transaction_hash)
                for r in rows
            ],
        )


@_database_operation
def _read_rows(conn: Any, stream: str, from_block: int, to_block: int) -> list[hypersync.LogRow]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT block_number, log_index, block_time, address,
                   topic0, topic1, topic2, topic3, data, transaction_hash
            FROM hypersync_logs
            WHERE stream = %s AND block_number >= %s AND block_number <= %s
            ORDER BY block_number, log_index
            """,
            (stream, from_block, to_block),
        )
        return [
            hypersync.LogRow(
                block_number=int(r[0]), log_index=int(r[1]), block_time=int(r[2]),
                address=r[3], topic0=r[4], topic1=r[5], topic2=r[6], topic3=r[7], data=r[8],
                transaction_hash=r[9],
            )
            for r in cur.fetchall()
        ]


def _merge(
    db_rows: list[hypersync.LogRow],
    live_rows: list[hypersync.LogRow],
    from_block: int,
    to_block: int,
) -> list[hypersync.LogRow]:
    """Union DB + live rows, dedup by (block, log_index), clip to range, sort."""
    by_key: dict[tuple[int, int], hypersync.LogRow] = {}
    for r in db_rows:
        by_key[(r.block_number, r.log_index)] = r
    for r in live_rows:
        by_key[(r.block_number, r.log_index)] = r
    out = [r for r in by_key.values() if from_block <= r.block_number <= to_block]
    out.sort(key=lambda r: (r.block_number, r.log_index))
    return out
