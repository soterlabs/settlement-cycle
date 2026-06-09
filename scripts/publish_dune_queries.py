#!/usr/bin/env python3
"""Publish the runtime SQL files in src/settle/queries/ to Dune as named,
public, executed saved queries — so the resulting URLs can be shared
with someone outside the team without them having to re-run anything
locally.

The set of files to publish is hardcoded below (the seven runtime
queries in src/settle/queries/). Each gets:
  - created on Dune as a public saved query (idempotent — see registry below)
  - executed with the default parameter set defined in PARAM_DEFAULTS
  - recorded in cache/dune_published.json so subsequent runs are no-ops

A registry file at ``cache/dune_published.json`` (path-keyed, tracked
in git) keeps the file → query_id mapping stable across team members.
That's how the published URLs stay consistent: anyone running this
script with their own DUNE_API_KEY will reuse the existing query_id
rather than creating a duplicate.

Usage:
  DUNE_API_KEY=... python scripts/publish_dune_queries.py
  DUNE_API_KEY=... python scripts/publish_dune_queries.py --force

  --force        For each file: PATCH the Dune query's SQL with the
                 local content + re-execute. URL stays stable. Use
                 after editing any SQL in src/settle/queries/.

Note: ``--force`` only re-publishes files that are already in the
registry. New files (never published) are created either way, on
both the default and ``--force`` runs.

Why this script and not the runtime ``_resolve_query_id``? The runtime
path keys by SHA of SQL content (so SQL edits create a NEW query) and
defaults to private. The publish workflow needs path-keyed (so URLs
are stable across edits) and public (so the shared URL is viewable
without granting per-account access).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Make ``src/settle`` importable regardless of where this script is invoked from.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from settle.extract.dune import (  # noqa: E402
    _create_query,
    _execute_query,
    _format_param,
    _sql_hash,
    _update_query_sql,
)


REGISTRY_PATH = REPO_ROOT / "cache" / "dune_published.json"
QUERIES_DIR   = REPO_ROOT / "src" / "settle" / "queries"
DUNE_QUERY_URL = "https://dune.com/queries/{query_id}"


# ─── Default parameters for each runtime query ─────────────────────
# These are the values used when *executing* each query as part of the
# publish step. The recipient lands on a results page populated with
# these param values; they can re-run with different params via Dune's
# UI without losing the shared URL.
#
# Reasonable demo defaults: chain=ethereum, Spark Eth ALM as the
# holder, USDC as the token (the Anchorage-relevant flow), broad date
# window covering Spark's lifetime to date.

# Common addresses (canonical, lowercase)
SPARK_ETH_ALM       = "0x1601843c5e9bc251a3272907010afa41fa18347e"
ANCHORAGE_ESCROW    = "0x49506c3aa028693458d6ee816b2ec28522946872"
USDC_ETH            = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDS_ETH            = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
UNISWAP_V3_NFPM     = "0xc36442b4a4522e871399cd717abdd847ab11fe88"

# Common dates
SPARK_START         = "2024-11-18"   # first frob on ALLOCATOR-SPARK-A
ETH_PIN_BLOCK       = 24783000       # ~ Q1 2026 EoM (refresh as needed)
ETH_FROM_BLOCK      = 21000000       # ~ early 2025

# Per-query parameter sets. Each entry is the kwargs dict passed to the
# query's substitution layer. Comments list alternative venues you can
# swap in by editing the dict and re-running with --force.
PARAM_DEFAULTS = {
    # Daily EoD blocks for Eth (used by every cum_balance / debt timeseries).
    "blocks_at_eod.sql": {
        "chain":      "ethereum",
        "start_date": SPARK_START,
        "end_date":   "2026-04-30",
        "pin_block":  ETH_PIN_BLOCK,
    },

    # Cumulative `Vat.ilks(<ilk>).Art × rate / RAY` daily.
    # Default ilk: ALLOCATOR-SPARK-A (Spark prime).
    # Alternatives: ALLOCATOR-GROVE-A, ALLOCATOR-OBEX-A — swap the
    # ilk_bytes32 value below.
    "debt_timeseries.sql": {
        "ilk_bytes32": "0x414c4c4f4341544f522d535041524b2d41000000000000000000000000000000",  # ALLOCATOR-SPARK-A
        "start_date":  SPARK_START,
        "pin_block":   ETH_PIN_BLOCK,
    },

    # Per-counterparty inflow timeseries to Spark Eth ALM in USDC.
    # Surfaces (among others) the Anchorage interest sweeps that PR 1
    # captures via `external_alm_sources`.
    # Alternatives:
    #   (chain, holder=Spark Base ALM, token=USDC) — Spark Base USDC
    #   (chain, holder=Grove ALM,      token=USDS) — Grove USDS at ALM
    "inflow_by_counterparty.sql": {
        "chain":      "ethereum",
        "holder":     SPARK_ETH_ALM,
        "token":      USDC_ETH,
        "start_date": SPARK_START,
        "pin_block":  ETH_PIN_BLOCK,
    },

    # SSR rate history (used by sky_revenue compute).
    "ssr_history.sql": {
        "start_date": SPARK_START,
        "pin_block":  ETH_PIN_BLOCK,
    },

    # Filtered transfer timeseries — same shape as inflow_by_counterparty
    # but with a min-amount filter (BUIDL-style yield mints noise floor).
    # Alternatives:
    #   (chain=base, holder=Spark Base ALM,    token=USDC)
    #   (chain=ethereum, holder=Grove Eth ALM, token=USDS)
    "transfer_timeseries.sql": {
        "chain":               "ethereum",
        "holder":              SPARK_ETH_ALM,
        "token":               USDC_ETH,
        "min_transfer_amount": 0,
        "start_date":          SPARK_START,
        "pin_block":           ETH_PIN_BLOCK,
    },

    # Uni V3 LP position events — needs real NFT IDs to return rows.
    # Placeholder [0] used here so the publish step succeeds; the
    # recipient will see an empty result and can re-run with real IDs
    # via Dune's params UI. Replace with Grove's E12 V3 NFT IDs once
    # we have them (see PRD §3 Cat F venue inventory).
    "v3_liquidity_events.sql": {
        "nfpm":             UNISWAP_V3_NFPM,
        "token_ids_padded": "0x0000000000000000000000000000000000000000000000000000000000000000",
        "from_block":       ETH_FROM_BLOCK,
        "pin_block":        ETH_PIN_BLOCK,
    },

    # Source-tagged inflow for one specific (from_addr, to_addr, token)
    # triple. Defaulting to the Anchorage→Spark-ALM USDC flow so the
    # recipient can see the realised interest stream that PR 1
    # classifies as Cat A yield.
    # Alternatives:
    #   (from=Sky AllocatorBuffer 0xc395d150…, to=Spark ALM, token=USDS) — fresh USDS draws
    #   (from=Sky LITE-PSM 0x37305b1c…, to=Spark ALM, token=USDC)         — PSM-swapped USDC
    "venue_inflow.sql": {
        "chain":      "ethereum",
        "from_addr":  ANCHORAGE_ESCROW,
        "to_addr":    SPARK_ETH_ALM,
        "token":      USDC_ETH,
        "start_date": SPARK_START,
        "pin_block":  ETH_PIN_BLOCK,
    },
}


def _load_registry() -> dict:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {}


def _save_registry(reg: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n")


def _entry_qid(entry) -> int:
    """Registry entries are either a bare int (legacy) or
    ``{"query_id": int, "sql_sha256": str}``."""
    return int(entry["query_id"]) if isinstance(entry, dict) else int(entry)


def _entry_sha(entry) -> str | None:
    return entry.get("sql_sha256") if isinstance(entry, dict) else None


def _execute(query_id: int, params: dict) -> str:
    """Format params + kick off an execution. Returns execution_id.

    Doesn't poll for results — the publish flow is fire-and-forget so
    the recipient sees a "running" → "completed" transition on the
    Dune URL within ~30s without us tying up the script. Set
    DUNE_PUBLISH_WAIT=1 in the env if you want to block on completion.
    """
    formatted = [{"key": k, **_format_param(v)} for k, v in params.items()]
    return _execute_query(query_id, formatted, "medium")


def _publish_one(
    rel_path: str,
    sql_path: Path,
    registry: dict,
    force: bool,
) -> tuple[int, str]:
    """Returns (query_id, state) where state ∈ {created, force_updated,
    skipped, STALE}.

    ``STALE`` means the local SQL no longer matches what was last
    published (sha mismatch) and ``--force`` wasn't given — the runtime
    (``_resolve_query_id``) refuses to execute such a query, so this
    state is reported as a failure by ``main``.
    """
    sql = sql_path.read_text()
    sha = _sql_hash(sql)
    name = f"[settle-msc] {sql_path.name}"
    params = PARAM_DEFAULTS.get(sql_path.name, {})

    if rel_path in registry and not force:
        recorded = _entry_sha(registry[rel_path])
        if recorded is not None and recorded != sha:
            return _entry_qid(registry[rel_path]), "STALE"
        return _entry_qid(registry[rel_path]), "skipped"

    if rel_path in registry and force:
        query_id = _entry_qid(registry[rel_path])
        _update_query_sql(query_id, sql, is_private=False)
        registry[rel_path] = {"query_id": query_id, "sql_sha256": sha}
        _save_registry(registry)
        if params:
            _execute(query_id, params)
        return query_id, "force_updated"

    # Not in registry → create + execute.
    query_id = _create_query(sql, name=name, is_private=False)
    registry[rel_path] = {"query_id": query_id, "sql_sha256": sha}
    _save_registry(registry)
    if params:
        _execute(query_id, params)
    return query_id, "created"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--force", action="store_true",
        help="Re-publish: PATCH each query's SQL with the local file + re-execute. URL stays stable.",
    )
    args = p.parse_args()

    registry = _load_registry()
    # Stable iteration order — sorted by file name so the printed table
    # matches the on-disk order.
    sql_paths = sorted(QUERIES_DIR.glob("*.sql"))
    if not sql_paths:
        print(f"error: no SQL files found in {QUERIES_DIR}", file=sys.stderr)
        return 2

    rows = []
    for sql_path in sql_paths:
        rel = str(sql_path.relative_to(REPO_ROOT))
        try:
            qid, state = _publish_one(rel, sql_path, registry, args.force)
        except Exception as e:
            print(f"  FAIL {rel}: {e}", file=sys.stderr)
            rows.append((rel, None, None, f"failed: {type(e).__name__}"))
            continue
        rows.append((rel, qid, DUNE_QUERY_URL.format(query_id=qid), state))
        # Light pacing so we don't trip Dune rate limits when publishing
        # all seven on a fresh registry.
        time.sleep(0.5)

    # Final summary table.
    print()
    print(f"{'file':40s}  {'query_id':>10s}  {'state':14s}  url")
    print("-" * 110)
    for rel, qid, url, state in rows:
        qid_s = str(qid) if qid is not None else "—"
        url_s = url or ""
        print(f"{rel:40s}  {qid_s:>10s}  {state:14s}  {url_s}")
    print()
    print(f"Registry: {REGISTRY_PATH.relative_to(REPO_ROOT)}")
    stale = [r[0] for r in rows if r[3] == "STALE"]
    if stale:
        print(
            "\nSTALE: local SQL differs from the last-published version for: "
            + ", ".join(stale)
            + "\nThe runtime refuses to execute these (DuneError). "
            "Re-run with --force (owning account's DUNE_API_KEY) to publish.",
            file=sys.stderr,
        )
    return 0 if (all(r[1] is not None for r in rows) and not stale) else 1


if __name__ == "__main__":
    sys.exit(main())
