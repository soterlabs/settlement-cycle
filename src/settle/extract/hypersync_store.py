"""Reorg-safe persistence for HyperSync log queries.

Sits between the HyperSync client (``hypersync.py``) and the domain sources. It
turns "re-query everything every run" into "fetch only new finalized blocks,
serve the rest from Postgres" — WITHOUT ever caching stale/reorg-prone data.

Design (see db/schema.sql):
  * A **stream** = one HyperSync log selection (chain + addresses + topics),
    hashed to a stable id. Rows are stored per ``(stream, block, log_index)``.
  * **Staleness guard:** rows are persisted only for blocks at or below
    ``chain_head − HYPERSYNC_REORG_MARGIN`` (finalized, cannot reorg). If a
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
from collections.abc import Callable
from typing import Any

import requests

from . import hypersync, postgres_store

_DEFAULT_REORG_MARGIN = 500


def _reorg_margin() -> int:
    return int(os.environ.get("HYPERSYNC_REORG_MARGIN", str(_DEFAULT_REORG_MARGIN)))


def _stream_key(chain: str, selections: list[dict[str, Any]]) -> str:
    blob = json.dumps({"chain": chain, "sel": selections}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def fetch_logs(
    chain: str,
    selections: list[dict[str, Any]],
    from_block: int,
    to_block: int,
    *,
    post: Callable[..., Any] = requests.post,
) -> list[hypersync.LogRow]:
    """Return all logs matching ``selections`` in ``[from_block, to_block]``.

    Reads finalized rows from Postgres when covered; fetches only the missing
    (incremental or first-time) range from HyperSync; never persists rows inside
    the reorg window.
    """
    if os.environ.get("HYPERSYNC_NO_STORE") == "1":
        return hypersync.query_logs(chain, selections, from_block, to_block, post=post).rows

    conn = postgres_store._get_conn()
    if conn is None:  # no DB → live pass-through (same as before the store existed)
        return hypersync.query_logs(chain, selections, from_block, to_block, post=post).rows

    stream = _stream_key(chain, selections)
    _ensure_schema(conn)
    cov = _get_coverage(conn, stream)  # (covered_from, covered_to) | None

    # Fully covered already → serve from DB, zero network.
    if cov is not None and cov[0] <= from_block and to_block <= cov[1]:
        return _read_rows(conn, stream, from_block, to_block)

    # Miss. Build the honest fetch plan — coverage must NEVER claim a block
    # range we didn't fetch:
    #  * request overlaps/touches existing coverage → fetch only the missing
    #    edge range(s); the merged coverage stays contiguous.
    #  * request DISJOINT from existing coverage → fetch the requested range
    #    and persist the rows, but leave coverage untouched. A LEAST/GREATEST
    #    widening here would claim the unfetched gap between the two islands
    #    and permanently serve later reads with logs silently missing.
    if cov is None:
        fetch_ranges = [(from_block, to_block)]
        new_cov: tuple[int, int] | None = (from_block, to_block)
    else:
        clo, chi = cov
        if from_block <= chi + 1 and to_block >= clo - 1:   # overlap / adjacent
            fetch_ranges = []
            if from_block < clo:
                fetch_ranges.append((from_block, clo - 1))
            if to_block > chi:
                fetch_ranges.append((chi + 1, to_block))
            new_cov = (min(from_block, clo), max(to_block, chi))
        else:                                               # disjoint island
            fetch_ranges = [(from_block, to_block)]
            new_cov = None

    live_rows: list[hypersync.LogRow] = []
    archive = 0
    for f_lo, f_hi in fetch_ranges:
        res = hypersync.query_logs(chain, selections, f_lo, f_hi, post=post)
        live_rows.extend(res.rows)
        archive = max(archive, res.archive_height)
    safe_ceiling = archive - _reorg_margin() if archive else -1

    if to_block > safe_ceiling:
        # Upper bound is inside the reorg window — serve live, store NOTHING.
        db_rows = _read_rows(conn, stream, from_block, to_block) if cov is not None else []
        return _merge(db_rows, live_rows, from_block, to_block)

    # Historical (to_block ≤ safe_ceiling): every fetched row is finalized.
    _persist(conn, stream, live_rows)
    if new_cov is not None:
        _set_coverage(conn, stream, *new_cov)
    return _read_rows(conn, stream, from_block, to_block)


# --------------------------------------------------------------------------
# Postgres helpers (thin; reuse postgres_store's connection + graceful state).
# --------------------------------------------------------------------------

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
            CREATE INDEX IF NOT EXISTS idx_hypersync_logs_stream_block
                ON hypersync_logs (stream, block_number);
            CREATE TABLE IF NOT EXISTS hypersync_coverage (
                stream TEXT PRIMARY KEY, covered_from BIGINT NOT NULL,
                covered_to BIGINT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )


def _get_coverage(conn: Any, stream: str) -> tuple[int, int] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT covered_from, covered_to FROM hypersync_coverage WHERE stream = %s",
            (stream,),
        )
        row = cur.fetchone()
    return (int(row[0]), int(row[1])) if row else None


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


def _persist(conn: Any, stream: str, rows: list[hypersync.LogRow]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO hypersync_logs
                (stream, block_number, log_index, block_time, address,
                 topic0, topic1, topic2, topic3, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (stream, block_number, log_index) DO NOTHING
            """,
            [
                (stream, r.block_number, r.log_index, r.block_time, r.address,
                 r.topic0, r.topic1, r.topic2, r.topic3, r.data)
                for r in rows
            ],
        )


def _read_rows(conn: Any, stream: str, from_block: int, to_block: int) -> list[hypersync.LogRow]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT block_number, log_index, block_time, address,
                   topic0, topic1, topic2, topic3, data
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
