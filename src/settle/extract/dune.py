"""Dune client. Reads SQL files from `queries/`, executes via Dune API, returns DataFrames.

Workflow:
1. SQL files in ``queries/`` are the source of truth (in git).
2. On first execution, the SQL is uploaded to Dune via `createDuneQuery` and the
   returned query ID is stored in a local registry (``~/.cache/msc-settle/dune_ids.json``)
   keyed by ``sha256(sql_content)``.
3. Subsequent calls re-use the cached query ID, binding parameters at execution time.
4. Results are cached on disk via the standard Extract cache.

Requires env var ``DUNE_API_KEY``.
"""

from __future__ import annotations

import datetime as dt
import errno
import hashlib
import json
import logging
import os
import re

_log = logging.getLogger(__name__)
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .cache import cache_dir, cached

DUNE_API_BASE = "https://api.dune.com/api/v1"
DEFAULT_PERFORMANCE = "medium"
DEFAULT_POLL_TIMEOUT_SEC = 300
DEFAULT_POLL_INTERVAL_SEC = 3


class DuneError(RuntimeError):
    """Raised on Dune API failures or query execution errors."""


def _api_key() -> str:
    key = os.environ.get("DUNE_API_KEY")
    if not key:
        raise RuntimeError("Missing env var DUNE_API_KEY")
    return key


def _headers() -> dict[str, str]:
    return {"X-Dune-Api-Key": _api_key(), "Content-Type": "application/json"}


def _registry_path() -> Path:
    return cache_dir() / "dune_ids.json"


def _load_registry() -> dict[str, int]:
    p = _registry_path()
    if p.exists():
        return json.loads(p.read_text())
    return {}


def _save_registry(reg: dict[str, int]) -> None:
    """Atomic write: tmp file + ``replace``. Caller is expected to hold
    the registry lock while doing the read-modify-write."""
    p = _registry_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(reg, indent=2, sort_keys=True))
    tmp.replace(p)


class _RegistryLock:
    """Cross-process exclusive lock on the registry file.

    Uses ``O_EXCL`` on a sentinel ``.lock`` file rather than pulling in a third-
    party dep. Two parallel ``settle run`` processes resolving the same SQL
    won't both create their own Dune queries — the loser waits, then re-reads
    the registry and finds the winner's mapping.
    """

    def __init__(self, path: Path):
        self._lock_path = path.with_suffix(path.suffix + ".lock")

    def __enter__(self) -> "_RegistryLock":
        deadline = time.time() + 30
        while True:
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except OSError as e:
                if e.errno != errno.EEXIST:
                    raise
                if time.time() > deadline:
                    # Stale lock — best-effort takeover. Acceptable since the
                    # only side-effect is re-creating a Dune query already
                    # registered under a now-orphaned hash entry.
                    try:
                        self._lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                time.sleep(0.1)

    def __exit__(self, *_: object) -> None:
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.strip().encode()).hexdigest()


def _infer_parameters(sql: str) -> list[dict[str, str]]:
    """Extract ``{{param}}`` placeholders from SQL and build Dune parameter defs.

    Dune's create-query endpoint requires every ``{{param}}`` used in the SQL to
    have a matching parameter definition in the request body — otherwise it returns
    400 "invalid query parameters". Type is inferred from context:
    - bare ``{{pin_block}}`` adjacent to a numeric comparison → ``number``
    - everything else → ``text``
    """
    names = list(dict.fromkeys(re.findall(r"\{\{(\w+)\}\}", sql)))
    result = []
    for name in names:
        # Heuristic: if the placeholder appears directly adjacent to a numeric
        # operator (<=, >=, =, <, >) without surrounding quotes, treat as number.
        in_numeric_ctx = bool(re.search(r"[<>=!]\s*\{\{" + name + r"\}\}", sql))
        result.append({
            "key": name,
            "type": "number" if in_numeric_ctx else "text",
            "value": "0" if in_numeric_ctx else "",
        })
    return result


def _create_query(sql: str, name: str, *, is_private: bool = True) -> int:
    """POST a new saved query to Dune. Returns the new query_id.

    Defaults to ``is_private=True`` to match the runtime cache path's
    behaviour (queries auto-created by ``_resolve_query_id`` are
    ephemeral helpers, not for sharing). Pass ``is_private=False`` from
    the publish workflow when creating the canonical, shareable
    versions of these SQL files.
    """
    r = requests.post(
        f"{DUNE_API_BASE}/query",
        headers=_headers(),
        json={
            "name": name,
            "query_sql": sql,
            "is_private": is_private,
            "parameters": _infer_parameters(sql),
        },
        timeout=30,
    )
    if not r.ok:
        raise DuneError(
            f"Dune create query '{name}' → HTTP {r.status_code}: {r.text[:400]}"
        )
    return int(r.json()["query_id"])


def _update_query_sql(
    query_id: int, sql: str, *, is_private: bool | None = None,
) -> None:
    """PATCH an existing Dune query's SQL (and optionally flip visibility).

    Preserves the ``query_id`` (and therefore the shareable URL). Used
    by the publish workflow's ``--force`` mode to push local SQL edits
    to the canonical Dune copy without breaking previously-shared links.
    """
    body: dict[str, Any] = {"query_sql": sql}
    if is_private is not None:
        body["is_private"] = is_private
    r = requests.patch(
        f"{DUNE_API_BASE}/query/{query_id}",
        headers=_headers(),
        json=body,
        timeout=30,
    )
    if not r.ok:
        raise DuneError(
            f"Dune update query {query_id} → HTTP {r.status_code}: {r.text[:400]}"
        )


def _published_query_ids() -> dict[str, int]:
    """Load ``cache/dune_published.json`` from the repo root (keyed by relative path).

    This file is committed to the repo and maps each SQL file's repo-relative
    path to a canonical public Dune query ID. Checking it first means no
    Dune API calls are needed on a fresh clone — no auto-create, no local
    registry bootstrap.
    """
    # sql_path lives at <repo>/src/settle/queries/<name>.sql
    # → go up 4 levels from this file: extract → settle → src → repo root
    repo_root = Path(__file__).resolve().parents[3]
    published = repo_root / "cache" / "dune_published.json"
    if published.exists():
        return json.loads(published.read_text())
    return {}


def _resolve_query_id(sql_path: Path) -> int:
    """Get-or-create the Dune query ID for this SQL file. Cached by SQL content hash.

    Lookup order:
    1. ``cache/dune_published.json`` (in-repo, keyed by repo-relative path) —
       no API call needed, works on a fresh clone.
    2. User-level registry at ``~/.cache/msc-settle/dune_ids.json`` (keyed by
       SQL content hash) — picks up any auto-created private copies.
    3. Auto-create a new private Dune query and cache the result.
    """
    # 1. Check the committed published-IDs file first.
    try:
        repo_root = Path(__file__).resolve().parents[3]
        rel_key = str(sql_path.resolve().relative_to(repo_root)).replace("\\", "/")
        published = _published_query_ids()
        if rel_key in published:
            return int(published[rel_key])
    except (ValueError, KeyError):
        pass

    sql = sql_path.read_text()
    sha = _sql_hash(sql)
    # 2. Quick path: hit the user cache before acquiring the lock.
    reg = _load_registry()
    if sha in reg:
        return reg[sha]
    with _RegistryLock(_registry_path()):
        # Re-read inside the lock — another process may have created the
        # mapping while we were waiting.
        reg = _load_registry()
        if sha in reg:
            return reg[sha]
        # 3. Auto-create a private Dune query (the ``_create_query`` default
        #    is ``is_private=True``; auto-created queries are ephemeral
        #    helpers keyed by SQL-content hash, not for sharing). The
        #    public-publish flow lives in ``scripts/publish_dune_queries.py``,
        #    which sets ``is_private=False`` explicitly and registers the
        #    result in ``cache/dune_published.json`` so other team members
        #    don't re-create the same query.
        query_id = _create_query(sql, name=f"settle/{sql_path.name}")
        reg[sha] = query_id
        _save_registry(reg)
    return query_id


def _execute_query(
    query_id: int,
    parameters: dict[str, Any],
    performance: str,
) -> str:
    body: dict[str, Any] = {"performance": performance}
    if parameters:
        body["query_parameters"] = parameters
    r = requests.post(
        f"{DUNE_API_BASE}/query/{query_id}/execute",
        headers=_headers(),
        json=body,
        timeout=30,
    )
    if not r.ok:
        raise DuneError(
            f"Dune execute {query_id} → HTTP {r.status_code}: {r.text[:400]}"
        )
    return r.json()["execution_id"]


def _poll_results(execution_id: str, timeout: int = DEFAULT_POLL_TIMEOUT_SEC) -> dict:
    deadline = time.time() + timeout
    elapsed = 0.0
    while time.time() < deadline:
        r = requests.get(
            f"{DUNE_API_BASE}/execution/{execution_id}/results",
            headers=_headers(),
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        state = body.get("state")
        if state == "QUERY_STATE_COMPLETED":
            _log.info("  Dune %s completed in %.1fs", execution_id[:12], elapsed)
            return body
        if state in {"QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"}:
            raise DuneError(f"Dune execution {execution_id} ended in state {state}: {body}")
        if elapsed == 0:
            _log.info("  Dune %s running... (state: %s)", execution_id[:12], state)
        elif elapsed % 15 < DEFAULT_POLL_INTERVAL_SEC:
            _log.info("  Dune %s still running after %.0fs (state: %s)", execution_id[:12], elapsed, state)
        time.sleep(DEFAULT_POLL_INTERVAL_SEC)
        elapsed += DEFAULT_POLL_INTERVAL_SEC
    raise DuneError(f"Dune execution {execution_id} timed out after {timeout}s")


def _format_param(value: Any) -> Any:
    """Convert a Python value to a JSON-native type for Dune's execute payload.

    Dune's /execute endpoint accepts ``query_parameters`` as a plain dict
    ``{param_name: value}`` where values must be JSON primitives:
    - ``bytes`` / ``bytearray`` → hex string with ``0x`` prefix (Dune parses
      this as varbinary in the SQL template).
    - ``bool`` → boolean (must come before int; bool is a subclass of int).
    - ``int`` / ``float`` / ``Decimal`` → number (int preferred for block
      numbers; Decimal converted to float — acceptable precision for params).
    - ``datetime`` (aware or naive) → ISO-8601 string.
    - ``date`` → ISO-8601 string (SQL templates wrap as ``DATE '{{x}}'``).
    - everything else → ``str()``.
    """
    from decimal import Decimal as _Dec
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, _Dec):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def _fetch_all_rows(execution_id: str) -> list[dict]:
    """Pull every row from a completed execution, following Dune's pagination
    cursor. Long-running queries (multi-year debt timeseries, large inflow
    histories) can exceed the per-call row cap; without this the tail is
    silently dropped and downstream sums are wrong.
    """
    body = _poll_results(execution_id)
    rows: list[dict] = list(body.get("result", {}).get("rows", []) or [])
    metadata = body.get("result", {}).get("metadata", {}) or {}
    expected = metadata.get("total_row_count")
    next_uri = body.get("next_uri")
    while next_uri:
        r = requests.get(next_uri, headers=_headers(), timeout=30)
        r.raise_for_status()
        body = r.json()
        page = body.get("result", {}).get("rows", []) or []
        rows.extend(page)
        next_uri = body.get("next_uri")
    if expected is not None and len(rows) != expected:
        raise DuneError(
            f"Dune execution {execution_id} pagination mismatch: "
            f"got {len(rows)} rows, expected {expected}"
        )
    return rows


@cached(source_id="dune.execute")
def execute_query(sql_path: Path, params: dict[str, Any], pin_block: int,
                  performance: str = DEFAULT_PERFORMANCE) -> pd.DataFrame:
    """Execute a saved Dune query and return its results as a DataFrame.

    `pin_block` is folded into the param set as `pin_block` and is also part of the
    cache key. `params` keys must match named parameters declared in the SQL file.
    Callers MUST NOT pass ``pin_block`` inside ``params`` — that's an alias for
    the positional argument and would silently get overwritten.
    """
    if "pin_block" in params:
        raise ValueError(
            "execute_query: pass pin_block as the positional arg, not via params"
        )
    query_id = _resolve_query_id(sql_path)
    _log.info("Dune query %s (id=%d) submitting...", sql_path.name, query_id)

    full_params = {**params, "pin_block": pin_block}
    dune_params = {k: _format_param(v) for k, v in full_params.items()}

    execution_id = _execute_query(query_id, dune_params, performance)
    rows = _fetch_all_rows(execution_id)
    _log.info("Dune query %s → %d rows", sql_path.name, len(rows))
    return pd.DataFrame(rows)
