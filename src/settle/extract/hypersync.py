"""Low-level Envio HyperSync client — raw log queries over HTTP.

HyperSync is a stateless query API (not an indexer): you POST a selection
(addresses + topic filters + block range) and it streams back matching logs,
paginating by a server-side time budget via ``next_block``. Auth is a bearer
``ENVIO_API_TOKEN`` (free at https://app.envio.dev/api-tokens; 401 without one).

This module is transport only — no decoding, no persistence. The reorg-safe
persistence layer is ``hypersync_store``; domain decoding lives in the
``normalize/sources/hypersync_*`` sources.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests

from .cache import cached

_DEFAULT_TIMEOUT = 40
_MAX_PAGES = 100_000  # runaway backstop

# chain value (domain.Chain.value) → HyperSync host.
HYPERSYNC_HOSTS: dict[str, str] = {
    "ethereum": "eth.hypersync.xyz",
    "base": "base.hypersync.xyz",
    "arbitrum": "arbitrum.hypersync.xyz",
    "optimism": "optimism.hypersync.xyz",
    "unichain": "unichain.hypersync.xyz",
    "avalanche_c": "avalanche.hypersync.xyz",
    "plume": "plume.hypersync.xyz",
    "monad": "monad.hypersync.xyz",
}

_DEFAULT_LOG_FIELDS = [
    "block_number", "log_index", "address",
    "topic0", "topic1", "topic2", "topic3", "data",
]
_DEFAULT_BLOCK_FIELDS = ["number", "timestamp"]


class HyperSyncError(RuntimeError):
    """Raised on HyperSync transport / auth / query errors."""


@dataclass(frozen=True)
class LogRow:
    block_number: int
    log_index: int
    block_time: int          # unix seconds, UTC
    address: str
    topic0: str | None
    topic1: str | None
    topic2: str | None
    topic3: str | None
    data: str


@dataclass
class QueryResult:
    rows: list[LogRow] = field(default_factory=list)
    archive_height: int = 0  # HyperSync's indexed chain head


def endpoint(chain: str) -> str:
    """Resolve the HyperSync ``/query`` URL for ``chain``.

    Override per chain with ``HYPERSYNC_URL_<CHAIN>`` (e.g. ``HYPERSYNC_URL_ETHEREUM``);
    ``HYPERSYNC_URL`` overrides ethereum (back-compat with the debt source / tests).
    """
    override = os.environ.get(f"HYPERSYNC_URL_{chain.upper()}")
    if override:
        return override
    if chain == "ethereum" and os.environ.get("HYPERSYNC_URL"):
        return os.environ["HYPERSYNC_URL"]
    host = HYPERSYNC_HOSTS.get(chain)
    if not host:
        raise HyperSyncError(f"No HyperSync host mapping for chain {chain!r}")
    return f"https://{host}/query"


def _token() -> str:
    tok = os.environ.get("ENVIO_API_TOKEN")
    if not tok:
        raise HyperSyncError(
            "Missing env var ENVIO_API_TOKEN (free token at "
            "https://app.envio.dev/api-tokens; HyperSync returns 401 without it)"
        )
    return tok


def to_int(v: Any) -> int:
    """HyperSync JSON returns numerics as hex strings ('0x..') or ints."""
    if isinstance(v, int):
        return v
    s = str(v)
    return int(s, 16) if s.startswith("0x") else int(s)


def query_logs(
    chain: str,
    selections: list[dict[str, Any]],
    from_block: int,
    to_block: int,
    *,
    log_fields: list[str] | None = None,
    block_fields: list[str] | None = None,
    post: Callable[..., Any] = requests.post,
) -> QueryResult:
    """Fetch all logs matching ``selections`` in ``[from_block, to_block]`` (inclusive).

    ``selections`` is HyperSync's ``logs`` array — each entry is
    ``{"address": [...], "topics": [[topic0...], [topic1...], ...]}``; multiple
    entries are OR'd. Pages are followed via ``next_block`` until ``to_block``.
    """
    lf = log_fields or _DEFAULT_LOG_FIELDS
    bf = block_fields or _DEFAULT_BLOCK_FIELDS
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}
    base = {
        "logs": selections,
        "field_selection": {"log": lf, "block": bf},
    }
    result = QueryResult()
    cursor = from_block
    end_exclusive = to_block + 1  # HyperSync to_block is exclusive
    for _ in range(_MAX_PAGES):
        if cursor >= end_exclusive:
            break
        body = {**base, "from_block": cursor, "to_block": end_exclusive}
        page = _execute(chain, body, headers, post)
        result.archive_height = max(result.archive_height, to_int(page.get("archive_height", 0) or 0))
        for group in page.get("data") or []:
            ts_by_block = {
                to_int(b["number"]): to_int(b["timestamp"])
                for b in (group.get("blocks") or [])
            }
            for lg in group.get("logs") or []:
                bn = to_int(lg["block_number"])
                ts = ts_by_block.get(bn)
                if ts is None:
                    # A log whose block entry is missing from the same
                    # response group would otherwise be dated 1970 and
                    # silently dropped by every ``block_date >= start``
                    # filter downstream (and persisted misdated).
                    raise HyperSyncError(
                        f"HyperSync {chain} response has a log at block {bn} "
                        f"with no matching block timestamp — refusing to "
                        f"misdate the row."
                    )
                result.rows.append(
                    LogRow(
                        block_number=bn,
                        log_index=to_int(lg.get("log_index", 0)),
                        block_time=ts,
                        address=(lg.get("address") or "").lower(),
                        topic0=_lower(lg.get("topic0")),
                        topic1=_lower(lg.get("topic1")),
                        topic2=_lower(lg.get("topic2")),
                        topic3=_lower(lg.get("topic3")),
                        data=lg.get("data") or "0x",
                    )
                )
        nxt = page.get("next_block")
        if nxt is None or to_int(nxt) <= cursor:
            break
        cursor = to_int(nxt)
    # Completeness check — Dune-parity semantics are "complete data up to
    # pin_block or FAIL". When the archive has not indexed the requested
    # range yet (archive lag, or a pin beyond the archive head), pagination
    # stops advancing at the archive height; returning the partial rows as
    # if complete silently understates every downstream cum series. Only
    # enforceable when the server reports archive_height (real HyperSync
    # always does; minimal test doubles may not).
    if cursor < end_exclusive and result.archive_height:
        raise HyperSyncError(
            f"HyperSync {chain} returned an incomplete range: pagination "
            f"stopped at block {cursor} < requested to_block {to_block} "
            f"(archive_height={result.archive_height}). "
            + ("The archive has not indexed the requested range yet — "
               "retry once it catches up."
               if result.archive_height < to_block
               else "The server stopped advancing inside an indexed range "
                    "(server anomaly).")
        )
    return result


def archive_height(chain: str, *, post: Callable[..., Any] = requests.post) -> int:
    """Current HyperSync-indexed chain head — a cheap zero-row probe."""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}
    body = {"from_block": 0, "to_block": 1, "logs": [], "field_selection": {"block": ["number"]}}
    return to_int(_execute(chain, body, headers, post).get("archive_height", 0) or 0)


@cached(source_id="hypersync.block_timestamp")
def block_timestamp(chain: str, block: int) -> int:
    """UNIX timestamp of a single ``block`` via HyperSync.

    ``include_all_blocks`` returns the block even with no matching logs. Cached
    (deterministic given chain+block) — the binary search reuses probes across
    dates/venues/primes. Verified byte-identical to ``extract.rpc.block_timestamp``.
    """
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}
    body = {
        "from_block": block,
        "to_block": block + 1,
        "include_all_blocks": True,
        "logs": [],
        "field_selection": {"block": ["number", "timestamp"]},
    }
    data = _execute(chain, body, headers, requests.post)
    for group in data.get("data") or []:
        for b in group.get("blocks") or []:
            if to_int(b["number"]) == block:
                return to_int(b["timestamp"])
    raise HyperSyncError(f"HyperSync {chain}: block {block} not returned")


@cached(source_id="hypersync.find_block_at_or_before")
def find_block_at_or_before(chain: str, target_ts: int) -> int:
    """Highest block on ``chain`` whose timestamp <= ``target_ts`` (unix, UTC).

    Binary search over HyperSync block timestamps — mirrors
    ``extract.rpc._find_block_at_or_before_rpc`` exactly, so the result is
    identical to the RPC resolver, but every probe hits HyperSync (fast, cheap,
    off the archive RPC — and works on chains whose RPC is lagging, e.g. monad).
    Result is cached, so repeat (chain, target_ts) is free.
    """
    # ``archive_height`` can report a head block whose data isn't yet
    # query-returnable (``include_all_blocks`` returns nothing for the very
    # tip). Step back to the newest block HyperSync will actually serve — for
    # historical settlement anchors this is still far above the target, so the
    # search result is unaffected.
    high = archive_height(chain)
    head_ts: int | None = None
    for _ in range(64):
        if high <= 0:
            break
        try:
            head_ts = block_timestamp(chain, high)
            break
        except HyperSyncError:
            high -= 16
    if head_ts is None:
        raise HyperSyncError(
            f"find_block_at_or_before({chain}): no returnable block near head "
            f"{archive_height(chain)}"
        )
    if head_ts <= target_ts:
        return high
    if block_timestamp(chain, 0) > target_ts:
        raise HyperSyncError(
            f"find_block_at_or_before({chain}, ts={target_ts}): target precedes "
            f"genesis (block 0 ts = {block_timestamp(chain, 0)})."
        )
    low = 0
    while low < high:
        mid = (low + high + 1) // 2
        if block_timestamp(chain, mid) <= target_ts:
            low = mid
        else:
            high = mid - 1
    return low


def _lower(v: Any) -> str | None:
    return v.lower() if isinstance(v, str) else v


def _execute(chain: str, body: dict[str, Any], headers: dict[str, str], post) -> dict[str, Any]:
    try:
        resp = post(endpoint(chain), json=body, headers=headers, timeout=_DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise HyperSyncError(f"HyperSync request failed: {exc}") from exc
    if not resp.ok:
        raise HyperSyncError(f"HyperSync {chain} -> HTTP {resp.status_code}: {resp.text[:400]}")
    data: dict[str, Any] = resp.json()
    return data
