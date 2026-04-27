"""On-disk cache for Extract. Keyed by SHA256(source_id, args). Pickle-backed.

The pin block is part of the cache key — re-runs at the same pin hit the cache.
Cache lives under `~/.cache/msc-settle/` by default; override via `SETTLE_CACHE_DIR`.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def cache_dir() -> Path:
    base = os.environ.get("SETTLE_CACHE_DIR", "~/.cache/msc-settle")
    p = Path(base).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


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


def cached(source_id: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: cache return value to disk by SHA256 of (source_id, args, kwargs).

    Cache disabled by env var ``SETTLE_NO_CACHE=1``.
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if os.environ.get("SETTLE_NO_CACHE") == "1":
                return fn(*args, **kwargs)
            key = _hash_args(source_id, args, kwargs)
            path = cache_dir() / f"{source_id}_{key}.pkl"
            if path.exists():
                with path.open("rb") as f:
                    return pickle.load(f)  # noqa: S301 — internal cache, trusted bytes
            result = fn(*args, **kwargs)
            tmp = path.with_suffix(".pkl.tmp")
            with tmp.open("wb") as f:
                pickle.dump(result, f)
            tmp.replace(path)
            return result

        return wrapper

    return decorator
