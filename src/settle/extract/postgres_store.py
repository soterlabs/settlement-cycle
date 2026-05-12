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

import base64
import logging
import math
import os
import pickle as _pickle
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
#
# Two flavours of "unavailable":
#   * Permanently disabled (``_state["disabled"] is True``) — DATABASE_URL
#     unset or ``psycopg`` not installed. Will never recover within this
#     process; ``get``/``put`` become no-ops.
#   * Transient failure (conn closed by server / network blip) — cached
#     connection is dropped, next ``_get_conn`` reconnects. Server-side
#     idle timeouts hit this path during long settlement runs.
# --------------------------------------------------------------------------

_lock = threading.Lock()
_state: dict[str, Any] = {"conn": None, "disabled": None}


def _is_healthy(conn: Any) -> bool:
    """Cheap liveness check on a cached psycopg connection.

    ``psycopg.Connection`` exposes ``.closed`` (bool: client-side close) and
    ``.broken`` (bool: server hung up). Either flag means we must reconnect
    before the next query. We avoid issuing a ``SELECT 1`` round-trip here —
    server-side state is the source of truth for ``.broken``.
    """
    if conn is None:
        return False
    try:
        if getattr(conn, "broken", False):
            return False
        if getattr(conn, "closed", False):
            return False
    except Exception:
        return False
    return True


def _drop_conn(reason: str) -> None:
    """Invalidate the cached connection so the next ``_get_conn`` reconnects.
    Safe to call from any thread / from inside ``get``/``put`` on error."""
    with _lock:
        conn = _state["conn"]
        _state["conn"] = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            _log.info("Postgres connection dropped (%s) — will reconnect on next call", reason)


def _get_conn() -> Any:
    """Lazy connect (or reconnect) to ``DATABASE_URL``.

    Returns ``None`` if Postgres is permanently disabled (no env var or no
    ``psycopg``). For transient failures — server idle-timeout, network
    blip — drops the dead connection and reconnects on the next call.
    """
    if _state["disabled"]:
        return None

    # Fast path: cached, healthy. Avoid taking the lock for the common case.
    conn = _state["conn"]
    if conn is not None and _is_healthy(conn):
        return conn

    with _lock:
        if _state["disabled"] is True:
            return None

        # Double-check under the lock — another thread may have already
        # reconnected, or invalidated the dead connection.
        conn = _state["conn"]
        if conn is not None and _is_healthy(conn):
            return conn
        if conn is not None and not _is_healthy(conn):
            try:
                conn.close()
            except Exception:
                pass
            _state["conn"] = None
            _log.info("Postgres connection went stale (closed/broken) — reconnecting")

        url = os.environ.get("DATABASE_URL")
        if not url:
            # Permanent: no DATABASE_URL means the user never opted in.
            _state["disabled"] = True
            return None
        try:
            import psycopg  # type: ignore[import-untyped]
        except ImportError:
            # Permanent: psycopg not installed in this env.
            _log.info("psycopg not installed — Postgres cache layer disabled")
            _state["disabled"] = True
            return None
        try:
            _state["conn"] = psycopg.connect(url, autocommit=True)
        except Exception as e:
            # Transient: don't flip ``disabled``. Next call retries.
            _log.warning("Postgres connect failed (%s) — will retry on next call", e)
            return None
        return _state["conn"]


def is_enabled() -> bool:
    """True when Postgres is available; used by sync scripts to fail-fast."""
    return _get_conn() is not None


# --------------------------------------------------------------------------
# Lossless JSON encoding for arbitrary Python payloads.
#
# Each cached source returns one of: Decimal, int, str, bytes, datetime/date,
# list/tuple, dict, or pandas.DataFrame (Dune query results). Postgres JSONB
# stores JSON natively, so we wrap non-JSON types in single-key envelopes
# like ``{"$decimal": "1017.65"}`` that round-trip losslessly.
#
# pandas.DataFrame is encoded via ``to_dict(orient="split")`` so that empty
# DataFrames preserve their column metadata (``orient="records"`` would
# silently drop columns when there are no rows). NaN / Inf floats are
# coerced to ``None`` — JSON has no NaN and psycopg's Jsonb adapter rejects
# them; ``None`` round-trips back to NaN inside the reconstructed DataFrame.
#
# ``Address`` (domain type) is encoded readably as ``{"$address": "<hex>"}``
# so SQL queries can match on it (e.g. ``payload->>'$address' = '…'``).
#
# Anything else (e.g. frozen dataclasses like ``V3PoolState``) falls through
# to a base64-pickle envelope ``{"$pickle": "<base64>"}``. Opaque in SQL
# but lossless; same trust model as the local pickle cache (only our code
# writes to this DB, so we trust the bytes we read back).
# --------------------------------------------------------------------------


def _address_cls() -> type:
    """Lazy import of the ``Address`` domain type to avoid coupling at
    module-load time (postgres_store sits below domain in the layering)."""
    from ..domain.primes import Address
    return Address


def encode_payload(value: Any) -> Any:
    """Recursively convert ``value`` into JSON-serializable form."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        # JSON has no NaN / Inf — coerce to None. Reconstructing a DataFrame
        # from None values yields NaN again, so it round-trips cleanly.
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, bytes | bytearray):
        return {"$bytes": bytes(value).hex()}
    if isinstance(value, datetime):
        # datetime must precede date — datetime is a subclass of date.
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, _address_cls()):
        return {"$address": value.value.hex()}
    if isinstance(value, tuple):
        return {"$tuple": [encode_payload(v) for v in value]}
    # pandas.DataFrame — duck-typed to avoid a hard pandas import at module
    # load time. The ``columns`` + ``to_dict`` check is specific enough that
    # no other commonly-cached type matches.
    if type(value).__name__ == "DataFrame" and hasattr(value, "to_dict") and hasattr(value, "columns"):
        return {"$dataframe": encode_payload(value.to_dict(orient="split"))}
    if isinstance(value, list):
        return [encode_payload(v) for v in value]
    if isinstance(value, dict):
        return {str(k): encode_payload(v) for k, v in value.items()}
    # Generic fallback: pickle any other type (frozen dataclasses, custom
    # domain objects, etc.). Opaque in SQL but lossless. Pickle.loads on
    # decode is safe under our trust model — only our code writes here.
    try:
        return {"$pickle": base64.b64encode(_pickle.dumps(value)).decode("ascii")}
    except Exception as e:
        raise TypeError(
            f"encode_payload: cannot encode type {type(value).__name__} "
            f"(pickle fallback also failed: {e}): {value!r}"
        ) from e


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
            if k == "$dataframe":
                import pandas as pd  # lazy import — only needed when decoding a DF
                spec = decode_payload(obj[k])
                return pd.DataFrame(
                    data=spec.get("data", []),
                    columns=spec.get("columns", []),
                    index=spec.get("index") or None,
                )
            if k == "$address":
                return _address_cls()(bytes.fromhex(obj[k]))
            if k == "$pickle":
                return _pickle.loads(base64.b64decode(obj[k]))
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
    A failure invalidates the cached connection so the *next* call reconnects.
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
        _drop_conn(f"read failure: {type(e).__name__}")
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
        _drop_conn(f"write failure: {type(e).__name__}")


def put_many(items: list[tuple[str, str, Any, Any]]) -> None:
    """Bulk-insert version of :func:`put`. Each item is ``(source, args_hash,
    args, payload)``.

    Uses ``executemany`` so 500+ inserts hit Postgres in one round-trip
    instead of N — important when the DB is behind a public proxy (Railway's
    TCP proxy adds ~50 ms per round-trip from local dev). Same idempotency
    and graceful-degradation contract as ``put()``.
    """
    conn = _get_conn()
    if conn is None or not items:
        return
    try:
        from psycopg.types.json import Jsonb  # type: ignore[import-untyped]
    except ImportError:
        return
    params = [
        (s, h, Jsonb(encode_payload(a)), Jsonb(encode_payload(p)))
        for s, h, a, p in items
    ]
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO raw_data (source, args_hash, args, payload) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (source, args_hash) DO NOTHING",
                params,
            )
    except Exception as e:
        _log.warning("Postgres put_many failed (%d rows): %s", len(items), e)
        _drop_conn(f"put_many failure: {type(e).__name__}")


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
