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

# Retry-on-429 policy. Dune throttles bursty workloads with HTTP 429 +
# (sometimes) a ``Retry-After`` header. Without retry, a single 429 mid-run
# cascades: DuneBlockResolver init fails → orchestrator falls back to RPC →
# every per-day block lookup becomes its own Dune query → 100× slowdown.
DUNE_429_MAX_ATTEMPTS = 8
DUNE_429_BASE_BACKOFF_SEC = 2.0   # 2, 4, 8, 16, 32, 60 (capped), 60, 60
DUNE_429_BACKOFF_CAP_SEC = 60.0


class DuneError(RuntimeError):
    """Raised on Dune API failures or query execution errors."""


def _request_with_429_retry(method: str, url: str, **kwargs) -> requests.Response:
    """``requests.{get,post}`` wrapper with retry on 429, ``ConnectionError``,
    and ``Timeout``. Exponential backoff; respects ``Retry-After`` on 429.

    Network flakes — DNS resolution failures, transient TCP resets, read
    timeouts on slow Dune responses — get the same backoff treatment as
    429s. Without this, a one-off ``Failed to resolve api.dune.com``
    crashes the whole monthly cell, even though a 2 s sleep would have
    let the resolver recover. The native ``timeout=`` kwarg passed by
    callers (typically 30 s) bounds the per-attempt wait; ``requests``
    raises ``ReadTimeout`` / ``ConnectionError`` when it fires.

    All other HTTP statuses (including 5xx) return after the first attempt —
    those are surfaced to callers via the usual ``raise_for_status`` /
    ``r.ok`` checks. We don't retry 5xx here because Dune's pattern is
    deterministic ``query-failed → expired/cancelled`` rather than
    transient transport flakes.
    """
    last_resp: requests.Response | None = None
    short_url = url.rsplit("/", 2)[-2] + "/" + url.rsplit("/", 1)[-1]
    for attempt in range(DUNE_429_MAX_ATTEMPTS):
        try:
            r = requests.request(method.upper(), url, **kwargs)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            if attempt == DUNE_429_MAX_ATTEMPTS - 1:
                raise
            wait_sec = min(
                DUNE_429_BASE_BACKOFF_SEC * (2 ** attempt),
                DUNE_429_BACKOFF_CAP_SEC,
            )
            _log.warning(
                "Dune %s %s — %s; waiting %.1fs (attempt %d/%d)",
                method.upper(), short_url, type(exc).__name__,
                wait_sec, attempt + 1, DUNE_429_MAX_ATTEMPTS,
            )
            time.sleep(wait_sec)
            continue
        if r.status_code != 429:
            return r
        last_resp = r
        # Honor Retry-After when present, otherwise exponential backoff.
        retry_after = r.headers.get("Retry-After")
        if retry_after is not None:
            try:
                wait_sec = float(retry_after)
            except ValueError:
                wait_sec = DUNE_429_BASE_BACKOFF_SEC
        else:
            wait_sec = min(
                DUNE_429_BASE_BACKOFF_SEC * (2 ** attempt),
                DUNE_429_BACKOFF_CAP_SEC,
            )
        if attempt == 0:
            _log.warning(
                "Dune 429 Too Many Requests on %s %s — waiting %.1fs (attempt 1/%d)",
                method.upper(), short_url,
                wait_sec, DUNE_429_MAX_ATTEMPTS,
            )
        elif attempt == DUNE_429_MAX_ATTEMPTS - 1:
            _log.warning(
                "Dune still 429ing after %d attempts — giving up; caller will see HTTP 429",
                DUNE_429_MAX_ATTEMPTS,
            )
        time.sleep(wait_sec)
    return last_resp  # type: ignore[return-value]


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
    have a matching parameter definition in the request body — otherwise it
    returns 400 "invalid query parameters".

    Type is inferred from the parameter **name** rather than its surrounding
    SQL context — the latter heuristic was fragile (it treated
    ``WHERE addr_col = {{holder}}`` as numeric because of the ``=``, then
    Dune rejected the address value at runtime). Block / amount / numeric-
    looking names → ``number``; everything else (addresses, hex, dates,
    timestamps, chain names, raw strings) → ``text``, which is the safe
    default for the Sources in this codebase.

    If you need to override for a specific param, pre-publish the query
    manually and add its ID to ``cache/dune_published.json``.
    """
    names = list(dict.fromkeys(re.findall(r"\{\{(\w+)\}\}", sql)))
    # Param-name patterns that should be typed as ``number`` on Dune.
    # Covers ``pin_block``, ``from_block``, ``to_block``, ``min_transfer_amount``,
    # ``max_count``, ``threshold``, ``limit``, etc. — and stays text for
    # addresses (``holder``, ``token``, ``nfpm``, ``from_addr``), dates, ISO
    # timestamps (``ts``, ``start_date``), and chain names.
    _NUMERIC_NAME = re.compile(
        r"(?:^|_)block$|^block_|amount$|amount_|_amount|count$|threshold$|^limit$|^min_|^max_"
    )
    result = []
    for name in names:
        is_number = bool(_NUMERIC_NAME.search(name))
        result.append({
            "key": name,
            "type": "number" if is_number else "text",
            "value": "0" if is_number else "",
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
        # 3. Auto-create the Dune query as PUBLIC (``is_private=False``).
        #    Dune's free / community tier caps private queries; on busy
        #    repos we'd hit HTTP 402 ("Max number of private queries
        #    reached") after creating ~25 ephemeral helpers and the whole
        #    settlement run would fail. Public queries don't count against
        #    that cap. There's no downside for us — these are auto-keyed
        #    by SQL-content hash; the public visibility just means the
        #    query body is browsable at ``dune.com/queries/<id>``, which
        #    matches what ``scripts/publish_dune_queries.py`` does for the
        #    intentionally-shared queries anyway.
        query_id = _create_query(
            sql, name=f"settle/{sql_path.name}", is_private=False,
        )
        reg[sha] = query_id
        _save_registry(reg)
    return query_id


_DUNE_QUOTA_EXHAUSTED: bool = False


def _execute_query(
    query_id: int,
    parameters: dict[str, Any],
    performance: str,
) -> str:
    # Once we've seen a 402 ("datapoint limit per billing cycle"), every
    # subsequent execute call within the same Python process will also
    # 402. Short-circuiting saves ~30s per call (one 429 retry + the
    # POST round-trip), which adds up to hours over a multi-month run
    # that fans out per-day Dune lookups. Cleared on process restart.
    global _DUNE_QUOTA_EXHAUSTED
    if _DUNE_QUOTA_EXHAUSTED:
        raise DuneError(
            f"Dune execute {query_id} → HTTP 402 (short-circuited; previous "
            f"call returned 402 — datapoint quota exhausted for billing cycle)"
        )
    body: dict[str, Any] = {"performance": performance}
    if parameters:
        body["query_parameters"] = parameters
    r = _request_with_429_retry(
        "POST",
        f"{DUNE_API_BASE}/query/{query_id}/execute",
        headers=_headers(),
        json=body,
        timeout=30,
    )
    if not r.ok:
        if r.status_code == 402:
            _DUNE_QUOTA_EXHAUSTED = True
        raise DuneError(
            f"Dune execute {query_id} → HTTP {r.status_code}: {r.text[:400]}"
        )
    return r.json()["execution_id"]


def _poll_results(execution_id: str, timeout: int = DEFAULT_POLL_TIMEOUT_SEC) -> dict:
    deadline = time.time() + timeout
    elapsed = 0.0
    while time.time() < deadline:
        r = _request_with_429_retry(
            "GET",
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
        r = _request_with_429_retry("GET", next_uri, headers=_headers(), timeout=30)
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
def _execute_query_cached(
    query_id: int, sql_path_str: str, params: dict[str, Any],
    pin_block: int, performance: str,
) -> pd.DataFrame:
    """Cache-keyed implementation. ``query_id`` is the FIRST arg so any
    re-mapping (e.g. ``cache/dune_published.json`` repointed to a different
    query) invalidates the cache automatically. ``sql_path_str`` is kept in
    the key for human-readable cache filenames but it's the ``query_id``
    that guarantees freshness when the SQL content changes.

    See the wrapper ``execute_query`` for the public entry point.
    """
    _log.info("Dune query %s (id=%d) submitting...", sql_path_str, query_id)
    # ``pin_block`` is part of the cache key (distinct EoM blocks → distinct
    # cache entries) but is NOT forwarded to Dune as a query parameter — Dune
    # validates submitted params against the query's declared schema and rejects
    # unknown names with HTTP 400.  The date-range filter in the SQL already
    # constrains results to the period; the pin_block upper-bound filter in SQL
    # was removed for the same reason (and is redundant given the date filter).
    dune_params = {k: _format_param(v) for k, v in params.items()}
    execution_id = _execute_query(query_id, dune_params, performance)
    rows = _fetch_all_rows(execution_id)
    _log.info("Dune query %s → %d rows", sql_path_str, len(rows))
    return pd.DataFrame(rows)


def execute_query(sql_path: Path, params: dict[str, Any], pin_block: int,
                  performance: str = DEFAULT_PERFORMANCE) -> pd.DataFrame:
    """Execute a saved Dune query and return its results as a DataFrame.

    `pin_block` is used only as part of the cache key (so that re-runs at a
    different EoM block get a fresh Dune result). It is NOT forwarded to Dune
    as a query parameter. `params` keys must match named parameters declared in
    the SQL file and in the saved Dune query's parameter schema.
    Callers MUST NOT pass ``pin_block`` inside ``params``.

    The cache key includes the resolved Dune ``query_id`` so that re-pointing
    ``cache/dune_published.json`` to a different query (e.g. migrating from
    a frob-only ``debt_timeseries`` query to a frob+grab one) invalidates any
    cached results automatically — no need for ``SETTLE_NO_CACHE=1``.
    """
    if "pin_block" in params:
        raise ValueError(
            "execute_query: pass pin_block as the positional arg, not via params"
        )
    query_id = _resolve_query_id(sql_path)
    return _execute_query_cached(
        query_id, sql_path.name, params, pin_block, performance,
    )
