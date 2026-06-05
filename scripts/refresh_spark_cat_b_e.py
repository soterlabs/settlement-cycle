"""Refresh Spark Cat B + Cat E cum_balance fixtures via published
`transfer_timeseries.sql` (Dune query 7432800).

The Q1 capture used a custom multi-venue query that's since been
archived. Use the published per-venue ``transfer_timeseries.sql`` to
get the same `(block_date, daily_net, cum_balance)` shape for each
venue, then merge with the existing fixture rows.

Usage:
    DUNE_API_KEY=... PYTHONPATH=src python3 scripts/refresh_spark_cat_b_e.py
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

TRANSFER_TIMESERIES_QID = 7432800  # published

# (venue_id, chain, token_address, holder=Spark ALM on that chain).
# Pulled from config/spark.yaml + the BA Labs wallet inventory.
SPARK_ALM = {
    "ethereum":    "0x1601843c5e9bc251a3272907010afa41fa18347e",
    "base":        "0x2917956eff0b5eaf030abdb4ef4296df775009ca",
    "arbitrum":    "0x92afd6f2385a90e44da3a8b60fe36f6cbe1d8709",
    "optimism":    "0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb",
    "unichain":    "0x345e368fccd62266b3f5f37c9a131fd1c39f5869",
    "avalanche_c": "0xece6b0e8a54c2f44e066fbb9234e7157b15b7fec",
}

# Pin block per chain — needs to be at-or-after 2026-05-31 EoM.
MAY_31_PIN_BLOCK = {
    "ethereum":    25300000,
    "base":        47000000,
    "arbitrum":   470000000,
    "optimism":   152500000,
    "unichain":    50000000,
    "avalanche_c": 87500000,
}

# Cat B venues (excluding S60 which uses CREATE2 same address as S56 but on
# avalanche — same vault wallet, different chain; same Dune chain string).
CAT_B_VENUES = [
    ("S10", "ethereum", "0x56a76b428244a50513ec81e225a293d128fd581d"),
    ("S11", "ethereum", "0xc7cdcfdefc64631ed6799c95e3b110cd42f2bd22"),
    ("S12", "ethereum", "0x73e65dbd630f90604062f6e02fab9138e713edd9"),
    ("S13", "ethereum", "0xe41a0583334f0dc4e023acd0bfef3667f6fe0597"),
    ("S14", "ethereum", "0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b"),
    ("S15", "ethereum", "0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d"),
    ("S16", "ethereum", "0x9d39a5de30e57443bff2a8307a4256c8797a3497"),
    ("S17", "ethereum", "0x2bbe31d63e6813e3ac858c04dae43fb2a72b0d11"),
    ("S18", "ethereum", "0x38464507e02c983f20428a6e8566693fe9e422a9"),
    ("S32", "ethereum", "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"),
    ("S34", "base",     "0x7bfa7c4f149e7415b73bdedfe609237e29cbf34a"),
    ("S36", "base",     "0xf62e339f21d8018940f188f6987bcdf02a849619"),
    ("S37", "base",     "0x5875eee11cf8398102fdad704c9e96607675467a"),
    ("S42", "arbitrum", "0x3459fcc94390c3372c0f7b4cd3f8795f0e5afe96"),
    ("S43", "arbitrum", "0xddb46999f8891663a8f2828d25298f70416d7610"),
    ("S47", "optimism", "0xb5b2dc7fd34c249f4be7fb1fcea07950784229e0"),
    ("S51", "unichain", "0xa06b10db9f390990364a3984c04fadf1c13691b5"),
]

# Cat E — all confirmed $0 by Q1 2026 per the original manifest. Refresh
# anyway for completeness; expect zero rows.
CAT_E_VENUES = [
    ("S19", "ethereum", "0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041"),  # BUIDL
    ("S20", "ethereum", "0x8c213ee79581ff4984583c6a801e5263418c4b86"),  # JTRSY
    ("S21", "ethereum", "0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"),  # USTB
    ("S22", "ethereum", "0x14d60e7fdc0d71d8611742720e4c50e7a974020c"),  # USCC
    # S23 Anchorage skipped — special holder_override, not at ALM
]


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{DUNE_BASE}{path}", data=json.dumps(body).encode(),
        headers=HEADERS, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _get(path: str) -> dict:
    req = urllib.request.Request(f"{DUNE_BASE}{path}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def execute_and_poll(query_id: int, params: dict) -> list[dict]:
    print(f"  Submitting query {query_id} with {params.get('holder','?')[-10:]}/{params.get('token','?')[-10:]}", flush=True)
    resp = _post(f"/query/{query_id}/execute",
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


def fetch_venue(venue_id: str, chain: str, token_addr: str) -> list[dict]:
    holder = SPARK_ALM[chain]
    token = "0x" + token_addr.replace("0x", "")
    pin = MAY_31_PIN_BLOCK[chain]
    rows = execute_and_poll(
        TRANSFER_TIMESERIES_QID,
        {
            "chain":               chain,
            "holder":              holder,
            "token":               token,
            "min_transfer_amount": "0",
            "start_date":          "2024-11-18",
            "pin_block":           str(pin),
        },
    )
    # Tag every row with venue_id and chain to match the fixture schema.
    for r in rows:
        r["venue_id"] = venue_id
        r["chain"] = chain
    return rows


def refresh(name: str, venues: list[tuple[str, str, str]], out_path: Path) -> None:
    print(f"\n=== {name} ({len(venues)} venues) ===", flush=True)
    all_rows: list[dict] = []
    for (vid, chain, addr) in venues:
        try:
            all_rows.extend(fetch_venue(vid, chain, addr))
        except Exception as e:
            print(f"  ERROR fetching {vid}: {e}", flush=True)

    # Sort by (venue_id, block_date) for stability.
    all_rows.sort(key=lambda r: (r["venue_id"], r["block_date"]))

    payload = {
        "_about": f"Spark {name} cum_balance — refreshed {time.strftime('%Y-%m-%d')} via published transfer_timeseries.sql (query 7432800), holder = chain-specific Spark ALM. Lifetime to 2026-05-31.",
        "_dune_query_id": TRANSFER_TIMESERIES_QID,
        "_columns": ["venue_id", "chain", "block_date", "daily_net", "cum_balance"],
        "_row_count": len(all_rows),
        "rows": all_rows,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"  wrote {out_path} ({len(all_rows)} rows)")


def main() -> int:
    refresh("Cat B", CAT_B_VENUES, FIXTURE_DIR / "cat_b_cum_balance.json")
    refresh("Cat E", CAT_E_VENUES, FIXTURE_DIR / "cat_e_cum_balance.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
