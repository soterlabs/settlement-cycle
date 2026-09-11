"""Connection helpers + schema bootstrap for the decoded store.

One connection string, ``DATABASE_URL`` (the same Postgres the cache layer
uses). The writer (cron) opens a plain connection; the API uses a small pool.
Schema is applied from ``db/schema.sql`` — every statement there is
``IF NOT EXISTS`` / ``ADD COLUMN IF NOT EXISTS``, so applying it is idempotent.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg

__all__ = ["apply_schema", "connect", "database_url", "schema_sql"]

_REPO = Path(__file__).resolve().parents[3]


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set — the decoded store needs Postgres")
    return url


def schema_sql() -> str:
    return (_REPO / "db" / "schema.sql").read_text()


def apply_schema(conn: Any) -> None:
    """Idempotent: creates the tables the pipeline and API need."""
    with conn.cursor() as cur:
        cur.execute(schema_sql())
    conn.commit()


@contextmanager
def connect(*, autocommit: bool = False) -> Iterator[psycopg.Connection[Any]]:
    conn = psycopg.connect(database_url(), autocommit=autocommit, connect_timeout=15)
    try:
        yield conn
    finally:
        conn.close()
