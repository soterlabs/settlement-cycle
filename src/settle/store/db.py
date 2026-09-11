"""Connection helpers + schema bootstrap for the decoded store.

One connection string, ``DATABASE_URL`` (the same Postgres the cache layer
uses). The writer (cron) opens a plain connection; the API uses a small pool.

Two things this module is deliberate about:

* **Timeouts.** The same keepalive / ``tcp_user_timeout`` / ``statement_timeout``
  settings ``extract.postgres_store`` applies, and for the same reason: a
  half-open socket to Railway's Postgres proxy otherwise wedges the caller in
  ``do_poll`` indefinitely (observed >1h hangs), which for the cron would mean
  a run stuck at ``status='running'`` forever.
* **Scope.** ``apply_schema`` applies ``db/schema_daily.sql`` — the tables this
  package owns — and nothing else. ``db/schema.sql`` carries an ``ALTER TABLE
  hypersync_logs``, which takes an ACCESS EXCLUSIVE lock on a table shared with
  the monthly pipeline; running that nightly would block its readers.
"""

from __future__ import annotations

import os
import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg

__all__ = ["apply_schema", "connect", "database_url", "schema_sql"]

_REPO = Path(__file__).resolve().parents[3]

# Mirrors extract.postgres_store._PG_CONNECT_KWARGS (minus autocommit, which is
# the caller's choice here): probe after 30 s idle, 10 s apart, 3 strikes; and
# a kernel-level 30 s unacked-data timeout that also catches half-open sockets
# on a *busy* connection.
_CONNECT_KWARGS: dict[str, Any] = {
    "connect_timeout": 10,
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
    "tcp_user_timeout": 30000,
}

# Server-side query timeout. Writes here are bulk ``executemany`` over tens of
# thousands of rows, so this is looser than postgres_store's 60 s point-lookup
# budget — but still bounded, so a stuck query cannot hold the run open.
_STATEMENT_TIMEOUT_MS = int(os.environ.get("SETTLE_STORE_STATEMENT_TIMEOUT_MS", "300000"))

# Connections whose schema has been applied in this process. Applying it is
# idempotent but not free (a dozen catalog lookups per call), and the cron and
# API both call it on paths that run per request / per task.
_SCHEMA_APPLIED: weakref.WeakSet[Any] = weakref.WeakSet()
_SCHEMA_LOCK = threading.Lock()


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set — the decoded store needs Postgres")
    return url


def schema_sql() -> str:
    """DDL for the tables this package owns (``db/schema_daily.sql``)."""
    return (_REPO / "db" / "schema_daily.sql").read_text()


def apply_schema(conn: Any) -> None:
    """Idempotent, and applied at most once per connection per process."""
    with _SCHEMA_LOCK:
        if conn in _SCHEMA_APPLIED:
            return
        with conn.cursor() as cur:
            cur.execute(schema_sql())
        conn.commit()
        _SCHEMA_APPLIED.add(conn)


def connect_kwargs(*, autocommit: bool = False) -> dict[str, Any]:
    """psycopg connect kwargs — also used to configure the API's pool."""
    return {**_CONNECT_KWARGS, "autocommit": autocommit}


def configure_session(conn: Any) -> None:
    """Per-session settings. Registered as the pool's connection configurer so
    pooled connections get the same statement timeout as direct ones."""
    autocommit = conn.autocommit
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = {_STATEMENT_TIMEOUT_MS}")
    if not autocommit:
        conn.commit()


@contextmanager
def connect(*, autocommit: bool = False) -> Iterator[psycopg.Connection[Any]]:
    conn = psycopg.connect(database_url(), **connect_kwargs(autocommit=autocommit))
    try:
        configure_session(conn)
        yield conn
    finally:
        conn.close()
