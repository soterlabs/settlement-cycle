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
import hashlib
import json
import os
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
    _registry_path().write_text(json.dumps(reg, indent=2, sort_keys=True))


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.strip().encode()).hexdigest()


def _create_query(sql: str, name: str) -> int:
    """POST a new saved query to Dune. Returns the new query_id."""
    r = requests.post(
        f"{DUNE_API_BASE}/query",
        headers=_headers(),
        json={"name": name, "query_sql": sql, "is_private": True, "is_temp": False},
        timeout=30,
    )
    r.raise_for_status()
    return int(r.json()["query_id"])


def _resolve_query_id(sql_path: Path) -> int:
    """Get-or-create the Dune query ID for this SQL file. Cached by SQL content hash."""
    sql = sql_path.read_text()
    sha = _sql_hash(sql)
    reg = _load_registry()
    if sha in reg:
        return reg[sha]
    query_id = _create_query(sql, name=f"settle/{sql_path.name}")
    reg[sha] = query_id
    _save_registry(reg)
    return query_id


def _execute_query(
    query_id: int,
    parameters: list[dict[str, Any]],
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
    r.raise_for_status()
    return r.json()["execution_id"]


def _poll_results(execution_id: str, timeout: int = DEFAULT_POLL_TIMEOUT_SEC) -> dict:
    deadline = time.time() + timeout
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
            return body
        if state in {"QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"}:
            raise DuneError(f"Dune execution {execution_id} ended in state {state}: {body}")
        time.sleep(DEFAULT_POLL_INTERVAL_SEC)
    raise DuneError(f"Dune execution {execution_id} timed out after {timeout}s")


def _format_param(value: Any) -> dict[str, Any]:
    """Format a Python value as a Dune query parameter dict.

    Convention (validated via MCP, 2026-04-27):
    - ``bytes`` (e.g. ilk_bytes32, addresses) → ``text`` with ``0x...`` value;
      Dune substitutes the literal text and the SQL parser interprets it as varbinary.
    - ``int`` / ``float`` → ``number``.
    - ``datetime`` (with tzinfo) → Dune ``datetime``.
    - ``date`` → ``text`` (so SQL templates can wrap it as ``DATE '{{x}}'``).
    - everything else → ``text`` via ``str()``.
    """
    # bool must come before int (bool is a subclass of int)
    if isinstance(value, bool):
        return {"type": "text", "value": str(value).lower()}
    if isinstance(value, (int, float)):
        return {"type": "number", "value": str(value)}
    if isinstance(value, bytes | bytearray):
        return {"type": "text", "value": "0x" + bytes(value).hex()}
    if isinstance(value, dt.datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, dt.date):
        return {"type": "text", "value": value.isoformat()}
    return {"type": "text", "value": str(value)}


@cached(source_id="dune.execute")
def execute_query(sql_path: Path, params: dict[str, Any], pin_block: int,
                  performance: str = DEFAULT_PERFORMANCE) -> pd.DataFrame:
    """Execute a saved Dune query and return its results as a DataFrame.

    `pin_block` is folded into the param set as `pin_block` and is also part of the
    cache key. `params` keys must match named parameters declared in the SQL file.
    """
    query_id = _resolve_query_id(sql_path)

    full_params = {**params, "pin_block": pin_block}
    dune_params = [
        {"key": k, **_format_param(v)} for k, v in full_params.items()
    ]

    execution_id = _execute_query(query_id, dune_params, performance)
    body = _poll_results(execution_id)

    rows = body.get("result", {}).get("rows", [])
    return pd.DataFrame(rows)
