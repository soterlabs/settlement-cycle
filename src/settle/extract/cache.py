"""On-disk cache for Extract. Keyed by SHA256(source_id, args). Pickle-backed.

The pin block is part of the cache key — re-runs at the same pin hit the cache.
Cache lives under `~/.cache/msc-settle/` by default; override via `SETTLE_CACHE_DIR`.

Layered storage (when ``DATABASE_URL`` is set):

    read:  pickle hit → return
           pickle miss → Postgres check
                         hit  → write pickle, return
                         miss → fetch upstream, write pickle + Postgres

The Postgres layer is the durable source of truth; the local pickle is a
fast LRU on top. With ``DATABASE_URL`` unset the pipeline behaves exactly
as before (pickle-only). See ``postgres_store.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import threading
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from . import postgres_store

P = ParamSpec("P")
R = TypeVar("R")


def cache_dir() -> Path:
    """Resolve and create the cache directory with owner-only permissions.

    The cache holds pickle blobs that are deserialized on read; if any user on
    the system can write into this directory, they can drop a malicious pickle
    and get arbitrary code execution next time the pipeline runs. Lock the
    directory down to mode ``0o700`` so only the owning user can read/write.
    """
    base = os.environ.get("SETTLE_CACHE_DIR", "~/.cache/msc-settle")
    p = Path(base).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    try:
        p.chmod(0o700)
    except OSError:
        # Some filesystems (e.g. shared NFS, Windows) don't honor chmod.
        # The chmod is defense-in-depth, not a hard requirement.
        pass
    return p


def _is_owned_by_current_user(path: Path) -> bool:
    """True if ``path`` is owned by the current user. On platforms without
    POSIX ownership semantics (Windows), assume True."""
    try:
        return path.stat().st_uid == os.getuid()
    except (AttributeError, OSError):
        return True


def _hash_args(source_id: str, args: tuple, kwargs: dict) -> str:
    """Stable SHA256 over (source, args, kwargs)."""
    payload = {
        "source": source_id,
        "args": [_jsonify(a) for a in args],
        "kwargs": {k: _jsonify(v) for k, v in sorted(kwargs.items())},
    }
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def _jsonify(x: Any) -> Any:
    """Best-effort canonical form for cache-key hashing."""
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, bytes | bytearray):
        return x.hex()
    if isinstance(x, Path):
        return str(x)
    if hasattr(x, "isoformat"):
        return x.isoformat()
    if isinstance(x, dict):
        return {str(k): _jsonify(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, list | tuple | set):
        return [_jsonify(v) for v in x]
    # Frozen dataclasses / value objects — fall back to a stable repr.
    return f"<{type(x).__name__}:{x!r}>"


def _write_pickle(path: Path, value: Any) -> None:
    """Atomic pickle write — per-(pid, tid) tmp suffix avoids two threads
    clobbering each other's partial dump (e.g. ThreadPoolExecutor in the
    Spark Q1 runner)."""
    tmp = path.with_suffix(f".pkl.{os.getpid()}.{threading.get_ident()}.tmp")
    with tmp.open("wb") as f:
        pickle.dump(value, f)
    tmp.replace(path)


def cached(source_id: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: cache return value by SHA256 of (source_id, args, kwargs).

    Lookup order: local pickle → Postgres (if ``DATABASE_URL`` set) →
    upstream fetch. Fresh fetches are written to both layers. Cache disabled
    entirely by env var ``SETTLE_NO_CACHE=1``.
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if os.environ.get("SETTLE_NO_CACHE") == "1":
                return fn(*args, **kwargs)
            key = _hash_args(source_id, args, kwargs)
            path = cache_dir() / f"{source_id}_{key}.pkl"
            # 1. Local pickle hit.
            if path.exists():
                # Only deserialize a pickle file we know we wrote — guards
                # against a tampered cache file dropped by another user.
                if not _is_owned_by_current_user(path):
                    raise RuntimeError(
                        f"Refusing to load cache file not owned by current user: {path}"
                    )
                with path.open("rb") as f:
                    return pickle.load(f)
            # 2. Postgres hit — populate the local LRU and return.
            pg_value = postgres_store.get(source_id, key)
            if pg_value is not postgres_store.MISS:
                _write_pickle(path, pg_value)
                return pg_value  # type: ignore[no-any-return]
            # 3. Upstream fetch + dual-write.
            result = fn(*args, **kwargs)
            _write_pickle(path, result)
            postgres_store.put(
                source_id, key,
                args={"args": [_jsonify(a) for a in args],
                      "kwargs": {k: _jsonify(v) for k, v in sorted(kwargs.items())}},
                payload=result,
            )
            return result

        return wrapper

    return decorator
