"""Capture inflow_by_counterparty fixture for Spark Cat A venues.

Cat A "raw idle" venues hold par-stable tokens (USDC/USDT/PYUSD/DAI/USDS/USDe)
at the chain-specific Spark ALM. The compute layer's Cat A capital-inflow
classifier (`_cat_a_capital_inflow_timeseries`) uses per-counterparty token
flows to distinguish:
  * inflows from a configured ``external_alm_sources`` (Anchorage etc.) —
    pass through as realized yield revenue
  * everything else — treated as value-preserving capital and netted out

Without this fixture, the loader returns empty rows and the compute layer
treats every Δvalue as yield, producing the bogus $483M Apr S27 revenue.

This script runs Dune query 7432797 (published ``inflow_by_counterparty.sql``)
for every Cat A venue × Spark ALM holder, merges results into a single JSON
fixture: ``tests/fixtures/spark_2026_q1/inflow_by_counterparty.json``.

Usage:
    DUNE_API_KEY=... python3 scripts/capture_spark_inflow_by_counterparty.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO / "tests" / "fixtures" / "spark_2026_q1"

DUNE_API_KEY = os.environ.get("DUNE_API_KEY")
if not DUNE_API_KEY:
    print("ERROR: DUNE_API_KEY env var required", file=sys.stderr)
    sys.exit(1)

DUNE_BASE = "https://api.dune.com/api/v1"
HEADERS = {"x-dune-api-key": DUNE_API_KEY, "content-type": "application/json"}
QUERY_ID = 7432797

SPARK_ALM = {
    "ethereum":    "0x1601843c5e9bc251a3272907010afa41fa18347e",
    "base":        "0x2917956eff0b5eaf030abdb4ef4296df775009ca",
    "arbitrum":    "0x92afd6f2385a90e44da3a8b60fe36f6cbe1d8709",
    "optimism":    "0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb",
    "unichain":    "0x345e368fccd62266b3f5f37c9a131fd1c39f5869",
    "avalanche_c": "0xece6b0e8a54c2f44e066fbb9234e7157b15b7fec",
}

# Safety pins at-or-after July 31 EoM (rows past the period are harmless;
# the runner slices by period). Updated 2026-08-03 for the July extension
# (July 31 EoM blocks: eth 25656292, base 49376526, arb 489802913,
# op 154971811, uni 54794040, avax 91716609).
PIN_BLOCK = {
    # ~2026-09-01 heads — safely past the August 31 EoM blocks (eth EoM
    # 25878704, base 50715726, arb 500455224, op 156311011, uni 57472440,
    # avax 94159927), and verified at-or-below each chain's current head.
    "ethereum":    25880000,
    "base":        50730000,
    "arbitrum":   500500000,
    "optimism":   156330000,
    "unichain":    57490000,
    "avalanche_c": 94180000,
}

# Cat A "raw idle" venues only (par-stable tokens at the ALM).
# (venue_id, chain, token_address). Excludes S31 USDS-on-Eth (it's the
# subproxy USDS that's already netted out of utilized — see PRD §17.11)
# and excludes par-stables that don't show up at the ALM (e.g. S38 USDS Base
# POL would still be captured for completeness).
CAT_A_VENUES = [
    ("S26", "ethereum",    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),  # USDC
    ("S27", "ethereum",    "0xdac17f958d2ee523a2206206994597c13d831ec7"),  # USDT
    ("S28", "ethereum",    "0x6c3ea9036406852006290770bedfcaba0e23a0e8"),  # PYUSD
    ("S29", "ethereum",    "0x6b175474e89094c44da98b954eedeac495271d0f"),  # DAI
    ("S30", "ethereum",    "0x4c9edd5852cd905f086c759e8383e09bff1e68b3"),  # USDe
    ("S31", "ethereum",    "0xdc035d45d973e3ec169d2276ddab16f1e407384f"),  # USDS-eth (POL)
    ("S38", "base",        "0x820c137fa70c8691f0e44dc420a5e53c168921dc"),  # USDS-base
    ("S39", "base",        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"),  # USDC-base
    ("S44", "arbitrum",    "0x6491c05a82219b8d1479057361ff1654749b876b"),  # USDS-arb
    ("S45", "arbitrum",    "0xaf88d065e77c8cc2239327c5edb3a432268e5831"),  # USDC-arb
    ("S48", "optimism",    "0x4f13a96ec5c4cf34e442b46bbd98a0791f20edc3"),  # USDS-op
    ("S49", "optimism",    "0x0b2c639c533813f4aa9d7837caf62653d097ff85"),  # USDC-op
    ("S52", "unichain",    "0x7e10036acc4b56d4dfca3b77810356ce52313f9c"),  # USDS-uni
    ("S53", "unichain",    "0x078d782b760474a361dda0af3839290b0ef57ad6"),  # USDC-uni
    ("S55", "avalanche_c", "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e"),  # USDC-ava
    # Added 2026-09-01 with the S64 venue — without a counterparty log a
    # Cat A venue's balance transits book as ±yield (the E13/E14 Grove
    # phantoms), and this one moved a quarter-billion in its first month.
    ("S64", "ethereum",    "0x8292bb45bf1ee4d140127049757c2e0ff06317ed"),  # RLUSD
]


def _post(path, body):
    req = urllib.request.Request(
        f"{DUNE_BASE}{path}", data=json.dumps(body).encode(),
        headers=HEADERS, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _get(path):
    req = urllib.request.Request(f"{DUNE_BASE}{path}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def execute_and_poll(params):
    print(f"  Submitting {params['chain']}/{params['holder'][-10:]}/{params['token'][-10:]}", flush=True)
    resp = _post(f"/query/{QUERY_ID}/execute",
                 {"query_parameters": params, "performance": "medium"})
    exec_id = resp["execution_id"]
    while True:
        time.sleep(3)
        st = _get(f"/execution/{exec_id}/status")
        state = st.get("state")
        if state == "QUERY_STATE_COMPLETED":
            break
        if state in ("QUERY_STATE_FAILED", "QUERY_STATE_EXPIRED"):
            raise RuntimeError(f"Dune failed: {st}")
    rows: list[dict] = []
    offset = 0
    while True:
        res = _get(f"/execution/{exec_id}/results?limit=1000&offset={offset}")
        page = res.get("result", {}).get("rows", [])
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    print(f"    → {len(rows)} rows", flush=True)
    return rows


def main() -> int:
    all_rows: list[dict] = []
    for vid, chain, token in CAT_A_VENUES:
        try:
            rows = execute_and_poll({
                "chain":      chain,
                "holder":     SPARK_ALM[chain],
                "token":      "0x" + token.replace("0x", ""),
                "start_date": "2024-11-18",
                "pin_block":  str(PIN_BLOCK[chain]),
            })
            for r in rows:
                r["venue_id"] = vid
                r["chain"] = chain
            all_rows.extend(rows)
        except Exception as e:
            print(f"  ERROR fetching {vid}: {e}", flush=True)
    all_rows.sort(key=lambda r: (r["venue_id"], r["block_date"], r.get("counterparty", "")))

    payload = {
        "_about": (
            f"Spark Cat A inflow_by_counterparty — captured {time.strftime('%Y-%m-%d')} "
            f"via published inflow_by_counterparty.sql (Dune query {QUERY_ID}). "
            "holder = chain-specific Spark ALM. Covers "
            # Derived, not hardcoded: the old literal said "→ 2026-07-31"
            # and silently went stale the moment the pin blocks advanced,
            # which is exactly the wrong thing for an audit artifact.
            f"{all_rows[0]['block_date'][:10] if all_rows else '?'} → "
            f"{max(r['block_date'][:10] for r in all_rows) if all_rows else '?'}."
        ),
        "_dune_query_id": QUERY_ID,
        "_columns": ["venue_id", "chain", "block_date", "counterparty", "signed_amount"],
        "_row_count": len(all_rows),
        "rows": all_rows,
    }
    out = FIXTURE_DIR / "inflow_by_counterparty.json"
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {out} ({len(all_rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
