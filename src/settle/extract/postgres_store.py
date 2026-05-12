"""Postgres backend for the raw-data cache.

Layered above the on-disk pickle cache in ``cache.py``. Reads are
read-through (pickle miss → Postgres check → upstream fetch); writes are
dual-write (every fresh fetch lands in both pickle and Postgres). The
Postgres row is keyed by ``(source, args_hash)`` and is append-only:
``INSERT … ON CONFLICT DO NOTHING`` so historical raw data is never
mutated by concurrent runs.

Graceful degradation: if ``DATABASE_URL`` is unset or psycopg isn't
available or the connection fails, this module returns ``MISS`` from
``get()`` and is a no-op in ``put()`` — the rest of the pipeline keeps
working on the local pickle cache alone. This makes Postgres an
opt-in layer (set ``DATABASE_URL`` to enable) without forcing a hard
dependency on every local dev.

Schema in ``db/schema.sql``; apply with::

    psql "$DATABASE_URL" -f db/schema.sql
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime
from decimal import Decimal
from typing import Any

_log = logging.getLogger(__name__)


# Sentinel returned by ``get()`` when the row is absent or Postgres is
# unavailable. Distinguishable from ``None`` (which is a valid cached payload
# for some sources).
class _Miss:
    __slots__ = ()
    def __repr__(self) -> str:
        return "<MISS>"


MISS = _Miss()


# --------------------------------------------------------------------------
# Connection management — lazy, single per-process, autocommit.
# --------------------------------------------------------------------------

_lock = threading.Lock()
_state: dict[str, Any] = {"conn": None, "disabled": None}


def _get_conn() -> Any:
    """Lazy connect to DATABASE_URL. Returns None when disabled."""
    if _state["disabled"]:
        return None
    with _lock:
        if _state["disabled"] is True:
            return None
        if _state["conn"] is not None:
            return _state["conn"]
        url = os.environ.get("DATABASE_URL")
        if not url:
            _state["disabled"] = True
            return None
        try:
            import psycopg  # type: ignore[import-untyped]
        except ImportError:
            _log.info("psycopg not installed — Postgres cache layer disabled")
            _state["disabled"] = True
            return None
        try:
            _state["conn"] = psycopg.connect(url, autocommit=True)
        except Exception as e:
            _log.warning("Postgres connect failed (%s) — disabling PG cache layer", e)
            _state["disabled"] = True
            return None
        _state["disabled"] = False
        return _state["conn"]


def is_enabled() -> bool:
    """True when Postgres is available; used by sync scripts to fail-fast."""
    return _get_conn() is not None


# --------------------------------------------------------------------------
# Lossless JSON encoding for arbitrary Python payloads.
#
# Each cached source returns one of: Decimal, int, str, bytes, datetime/date,
# list/tuple, dict (with the above as leaves). Postgres JSONB stores JSON
# natively, so we wrap non-JSON types in single-key envelopes like
# ``{"$decimal": "1017.65"}`` that round-trip losslessly.
# --------------------------------------------------------------------------

def encode_payload(value: Any) -> Any:
    """Recursively convert ``value`` into JSON-serializable form."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, bytes | bytearray):
        return {"$bytes": bytes(value).hex()}
    if isinstance(value, datetime):
        # datetime must precede date — datetime is a subclass of date.
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, tuple):
        return {"$tuple": [encode_payload(v) for v in value]}
    if isinstance(value, list):
        return [encode_payload(v) for v in value]
    if isinstance(value, dict):
        return {str(k): encode_payload(v) for k, v in value.items()}
    raise TypeError(f"encode_payload: unsupported type {type(value).__name__}: {value!r}")


_DECODERS = {
    "$decimal":  Decimal,
    "$bytes":    bytes.fromhex,
    "$datetime": datetime.fromisoformat,
    "$date":     date.fromisoformat,
}


def decode_payload(obj: Any) -> Any:
    """Inverse of ``encode_payload`` — reconstruct Python types from JSON."""
    if isinstance(obj, dict):
        if len(obj) == 1:
            (k,) = obj.keys()
            if k in _DECODERS:
                return _DECODERS[k](obj[k])
            if k == "$tuple":
                return tuple(decode_payload(v) for v in obj[k])
        return {k: decode_payload(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode_payload(v) for v in obj]
    return obj


# --------------------------------------------------------------------------
# get / put — the only API consumed by ``cache.py``.
# --------------------------------------------------------------------------

def get(source: str, args_hash: str) -> Any:
    """Return the cached payload for ``(source, args_hash)``.

    Returns ``MISS`` if the row doesn't exist, Postgres is unavailable, or
    the read fails (we never error-out the calling pipeline for a cache miss).
    """
    conn = _get_conn()
    if conn is None:
        return MISS
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM raw_data WHERE source = %s AND args_hash = %s",
                (source, args_hash),
            )
            row = cur.fetchone()
    except Exception as e:
        _log.warning("Postgres read failed for %s/%s: %s", source, args_hash[:12], e)
        return MISS
    if row is None:
        return MISS
    return decode_payload(row[0])


def put(source: str, args_hash: str, args: Any, payload: Any) -> None:
    """Insert ``(source, args_hash, args, payload)``. No-op on conflict or
    when Postgres is unavailable. Both ``args`` and ``payload`` are encoded
    losslessly before insert."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        from psycopg.types.json import Jsonb  # type: ignore[import-untyped]
    except ImportError:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw_data (source, args_hash, args, payload) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (source, args_hash) DO NOTHING",
                (source, args_hash, Jsonb(encode_payload(args)), Jsonb(encode_payload(payload))),
            )
    except Exception as e:
        _log.warning("Postgres write failed for %s/%s: %s", source, args_hash[:12], e)


# --------------------------------------------------------------------------
# Schema setup — used by the sync script + tests.
# --------------------------------------------------------------------------

def apply_schema(schema_sql: str) -> None:
    """Apply ``schema_sql`` (CREATE TABLE IF NOT EXISTS …) on the configured
    connection. Idempotent. Raises if Postgres is unavailable — callers that
    need fail-fast behavior should use this rather than relying on get/put's
    graceful degradation."""
    conn = _get_conn()
    if conn is None:
        raise RuntimeError(
            "Postgres unavailable (DATABASE_URL unset, psycopg missing, or "
            "connection failed) — cannot apply schema."
        )
    with conn.cursor() as cur:
        cur.execute(schema_sql)


def _reset_for_tests() -> None:
    """Drop the cached connection — used by tests that swap DATABASE_URL."""
    with _lock:
        conn = _state["conn"]
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _state["conn"] = None
        _state["disabled"] = None
