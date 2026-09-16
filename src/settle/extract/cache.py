"""On-disk cache for Extract. Keyed by SHA256(source_id, args). Pickle-backed.

The pin block is part of the cache key — re-runs at the same pin hit the cache.
Cache lives under `~/.cache/msc-settle/` by default; override via `SETTLE_CACHE_DIR`.

Layered storage (when ``DATABASE_URL`` is set):

    read:  pickle hit → return
           pickle miss → Postgres check
                         hit  → write pickle, return
                         miss → fetch upstream, write pickle + Postgres

With SETTLE_REQUIRE_POSTGRES=1, read Postgres first and fetch on a miss; local-only
files are never promoted into the required database.

The Postgres layer is the durable source of truth; the local pickle is a
fast local cache on top. With ``DATABASE_URL`` unset the pipeline behaves exactly
as before (pickle-only). See ``postgres_store.py``.
"""

from __future__ import annotations

import hashlib
import inspect
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
        signature = inspect.signature(fn)
        pinned = "chain" in signature.parameters and "block" in signature.parameters
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            from .input_cache import current_scope, input_revision, revision_key

            scope = current_scope()
            if scope is not None and source_id in {
                "hypersync.block_timestamp", "hypersync.find_block_at_or_before",
            }:
                # Some source internals resolve their own opening anchors.
                # They must inherit the run's policy, not use legacy caches.
                from . import hypersync
                from .hypersync_store import _reorg_margin
                bound = signature.bind(*args, **kwargs)
                if source_id == "hypersync.block_timestamp":
                    return hypersync.finalized_block_timestamp(
                        bound.arguments["chain"], bound.arguments["block"], _reorg_margin(),
                    )
                return hypersync.find_finalized_block_at_or_before(
                    bound.arguments["chain"], bound.arguments["target_ts"], _reorg_margin(),
                )
            source = source_id
            key_args, key_kwargs = args, kwargs
            if scope is not None and pinned and not source_id.startswith("hypersync."):
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                scope.check_block(bound.arguments["chain"], bound.arguments["block"])
                source = f"finalized.v2.{scope.revision}.{source_id}"
                # Equivalent positional/keyword calls share one persistent key.
                key_args, key_kwargs = (), bound.arguments
            elif input_revision() != "0":
                source = f"revision.{revision_key()}.{source_id}"
            if os.environ.get("SETTLE_NO_CACHE") == "1":
                if postgres_store.required():
                    raise postgres_store.PersistenceError("SETTLE_NO_CACHE conflicts with SETTLE_REQUIRE_POSTGRES")
                return fn(*args, **kwargs)
            key = _hash_args(source, key_args, key_kwargs)
            path = cache_dir() / f"{source}_{key}.pkl"
            encoded_args = {"args": [_jsonify(a) for a in key_args],
                            "kwargs": {k: _jsonify(v) for k, v in sorted(key_kwargs.items())}}
            # Required mode trusts only this database's durable entries.
            # Local files may belong to another DB, fixture run or revision
            # environment; never promote an unverified local-only value.
            if postgres_store.required():
                pg_value = postgres_store.get(source, key)
                if pg_value is not postgres_store.MISS:
                    _write_pickle(path, pg_value)
                    return pg_value
            # 1. Local pickle hit.
            if path.exists() and not postgres_store.required():
                # Only deserialize a pickle file we know we wrote — guards
                # against a tampered cache file dropped by another user.
                if not _is_owned_by_current_user(path):
                    raise RuntimeError(
                        f"Refusing to load cache file not owned by current user: {path}"
                    )
                with path.open("rb") as f:
                    value = pickle.load(f)
                return value
            # 2. Postgres hit — populate the local cache and return.
            if not postgres_store.required():
                pg_value = postgres_store.get(source, key)
                if pg_value is not postgres_store.MISS:
                    _write_pickle(path, pg_value)
                    return pg_value  # type: ignore[no-any-return]
            # 3. Upstream fetch + dual-write.
            failures = scope.failures if scope is not None else 0
            try:
                result = fn(*args, **kwargs)
            except Exception:
                if scope is not None:
                    scope.failures += 1
                raise
            # A nested failure may have been caught as a capability fallback
            # or zero. Do not turn that fallback into a durable success.
            if scope is not None:
                if result is None:
                    scope.failures += 1
                if scope.failures != failures or scope.fatal_error is not None:
                    return result
            postgres_store.put(
                source, key,
                args=encoded_args,
                payload=result,
            )
            _write_pickle(path, result)
            return result

        return wrapper

    return decorator
