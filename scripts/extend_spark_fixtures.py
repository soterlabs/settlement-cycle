"""Extend ``tests/fixtures/spark_2026_q1/`` fixture files to cover Apr+May 2026.

Refreshes (additively) the following files using the published Dune queries:
  * ``debt_timeseries.json``               — Dune query 7642450
  * ``eth_avalanche_daily_eod_blocks.json`` — Dune query 7474490
  * ``l2_daily_eod_blocks.json``           — Dune query 7474490

Cat B / Cat E cum_balance JSONs are NOT refreshed (would require
re-capturing the per-venue queries that were auto-created at Q1 time and
have since been archived). The runner is robust to no in-period rows
because ``SETTLE_SPARK_ALLOW_PRE_PERIOD_ANCHOR=1`` is set inside
``scripts/run_spark_2026.py`` — value_eom comes from RPC ``balanceOf``
at pin blocks, which doesn't depend on the cum_balance fixture.

Usage:
    DUNE_API_KEY=... python3 scripts/extend_spark_fixtures.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import urllib.request
import urllib.error

REPO = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO / "tests" / "fixtures" / "spark_2026_q1"

DUNE_API_KEY = os.environ.get("DUNE_API_KEY")
if not DUNE_API_KEY:
    print("ERROR: DUNE_API_KEY env var required", file=sys.stderr)
    sys.exit(1)

DUNE_BASE = "https://api.dune.com/api/v1"
HEADERS = {"x-dune-api-key": DUNE_API_KEY, "content-type": "application/json"}

# Spark ALLOCATOR-A ilk
ILK_BYTES32 = "0x414c4c4f4341544f522d535041524b2d41000000000000000000000000000000"

# Pin blocks at May 31 EoM (= upper bound for the extended fetch) — these
# should be at-or-after the actual May 31 23:59:59 UTC blocks. Adding a
# small buffer for safety.
MAY_31_PIN_BLOCK = {
    "ethereum":    25300000,  # ~2026-06-02
    "base":        47000000,
    "arbitrum":   470000000,  # arbitrum block numbers are 9-figure already by Q4 2025
    "optimism":   152500000,  # above the actual May EoM Op block 152336611
    "unichain":    73000000,
    "avalanche_c": 87500000,
}

# Spark prime needs blocks on these chains.
SPARK_CHAINS_DUNE = {
    "ethereum":    "ethereum",
    "base":        "base",
    "arbitrum":    "arbitrum",
    "optimism":    "optimism",
    "unichain":    "unichain",
    "avalanche_c": "avalanche_c",
}


def _post_json(url: str, body: dict[str, Any], timeout: int = 30) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def execute_and_poll(query_id: int, params: dict[str, Any]) -> list[dict]:
    """Submit an execution and poll until results land. Returns the rows."""
    print(f"  Executing query {query_id} with params {list(params.keys())}")
    body = {"query_parameters": params, "performance": "medium"}
    resp = _post_json(f"{DUNE_BASE}/query/{query_id}/execute", body)
    exec_id = resp["execution_id"]
    print(f"  exec_id={exec_id}, polling...")

    while True:
        time.sleep(3)
        st = _get_json(f"{DUNE_BASE}/execution/{exec_id}/status")
        state = st.get("state")
        if state == "QUERY_STATE_COMPLETED":
            break
        if state == "QUERY_STATE_FAILED":
            raise RuntimeError(f"Dune execution failed: {st}")
        if state == "QUERY_STATE_EXPIRED":
            raise RuntimeError(f"Dune execution expired: {st}")
        # else PENDING / EXECUTING — keep polling

    # Fetch all rows (paginated; ~10k per page, increase via limit)
    rows: list[dict] = []
    offset = 0
    limit = 1000
    while True:
        res = _get_json(
            f"{DUNE_BASE}/execution/{exec_id}/results"
            f"?limit={limit}&offset={offset}"
        )
        page = res.get("result", {}).get("rows", [])
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
    print(f"  fetched {len(rows)} rows")
    return rows


def refresh_debt_timeseries() -> None:
    """Re-run debt_timeseries (frob+grab) for Spark ilk through May 31 EoM."""
    rows = execute_and_poll(
        7642450,
        {
            "ilk_bytes32": ILK_BYTES32,
            "start_date":  "2024-11-18",
            "pin_block":   str(MAY_31_PIN_BLOCK["ethereum"]),
        },
    )

    # Reshape to match the existing fixture schema:
    #   {"_about": ..., "rows": [...]}, with cum_debt as DECIMAL strings.
    # The query already returns block_date, daily_dart, cum_debt — types
    # land as strings from the Dune API.
    out = {
        "_about": "Spark ALLOCATOR-A debt timeseries (frob+grab), 2024-11-18 → 2026-05-31. Refreshed via extend_spark_fixtures.py.",
        "_dune_query_id": 7642450,
        "_columns": ["block_date", "daily_dart", "cum_debt"],
        "_units": "USDS (18 decimals, human units)",
        "rows": rows,
    }
    dest = FIXTURE_DIR / "debt_timeseries.json"
    dest.write_text(json.dumps(out, indent=2))
    print(f"  wrote {dest} ({len(rows)} rows)")


def refresh_blocks() -> None:
    """Re-run blocks_at_eod (7474490) per chain, write into the two fixture files."""
    by_chain: dict[str, list[dict]] = {}
    for our_chain, dune_chain in SPARK_CHAINS_DUNE.items():
        pin = MAY_31_PIN_BLOCK[our_chain]
        rows = execute_and_poll(
            7474490,
            {
                "chain":      dune_chain,
                # Matches the existing fixture's start of 2025-12-31 so we
                # avoid bloating the file with pre-Q1 dates the runner never
                # consults.
                "start_date": "2025-12-31",
                "end_date":   "2026-05-31",
                "pin_block":  str(pin),
            },
        )
        by_chain[our_chain] = rows
        time.sleep(1)  # gentle pacing

    # Split into two files: ethereum + avalanche_c → eth_avalanche_daily_eod_blocks.json,
    # base/arbitrum/optimism/unichain → l2_daily_eod_blocks.json. This mirrors
    # the existing q1 fixture split.
    eth_ava_rows = []
    for chain in ("ethereum", "avalanche_c"):
        # Tag each row with `chain` to match the existing schema.
        for r in by_chain.get(chain, []):
            eth_ava_rows.append({
                "chain":        chain,
                "block_date":   r["block_date"],
                "block_number": int(r["block_number"]) if isinstance(r["block_number"], (int, str)) else r["block_number"],
            })
    l2_rows = []
    for chain in ("base", "arbitrum", "optimism", "unichain"):
        for r in by_chain.get(chain, []):
            l2_rows.append({
                "chain":        chain,
                "block_date":   r["block_date"],
                "block_number": int(r["block_number"]) if isinstance(r["block_number"], (int, str)) else r["block_number"],
            })

    # Sort each file by (chain, block_date) for stability.
    eth_ava_rows.sort(key=lambda r: (r["chain"], r["block_date"]))
    l2_rows.sort(key=lambda r: (r["chain"], r["block_date"]))

    eth_path = FIXTURE_DIR / "eth_avalanche_daily_eod_blocks.json"
    eth_path.write_text(json.dumps({
        "_about": "Daily EoD blocks for ethereum + avalanche_c, 2024-11-18 → 2026-05-31. Refreshed via extend_spark_fixtures.py.",
        "_dune_query_id": 7474490,
        "_columns": ["chain", "block_date", "block_number"],
        "rows": eth_ava_rows,
    }, indent=2))
    print(f"  wrote {eth_path} ({len(eth_ava_rows)} rows)")

    l2_path = FIXTURE_DIR / "l2_daily_eod_blocks.json"
    l2_path.write_text(json.dumps({
        "_about": "Daily EoD blocks for base/arbitrum/optimism/unichain, 2024-11-18 → 2026-05-31. Refreshed via extend_spark_fixtures.py.",
        "_dune_query_id": 7474490,
        "_chains": ["base", "arbitrum", "optimism", "unichain"],
        "_columns": ["chain", "block_date", "block_number"],
        "rows": l2_rows,
    }, indent=2))
    print(f"  wrote {l2_path} ({len(l2_rows)} rows)")


def main() -> int:
    print("Refreshing Spark fixtures (Q1 → Q2 extension)")
    print()

    print("1. debt_timeseries.json")
    refresh_debt_timeseries()
    print()

    print("2. eth_avalanche + l2 blocks")
    refresh_blocks()
    print()

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
