"""Compare our Spark settlements vs BA Labs balance-sheet API per-venue.

Usage:
    python3 scripts/compare_spark_vs_balabs.py [month_eom_date ...]

If no dates supplied, defaults to all per-month EoM dates 2026-01-31 ..
2026-05-31 (whichever provenance files exist).

Matching strategy (per Spark venue):
  * For most Cat A/B/C venues, the venue is held at the prime's ALM
    address on its chain. We sum BA Labs ``assets`` rows where
    ``(wallet_address, token_address, network)`` matches.
  * For Spark Savings V2 (S56–S60), our ``token.address`` IS the vault
    wallet — match by ``wallet_address``.
  * For S23 Anchorage, match by ``category=anchorage`` on the Spark Eth
    ALM (BA Labs uses a synthetic ``token_address`` for the off-chain
    exposure).
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
BA_LABS_URL = "https://observatory.data.blockanalitica.com/primes/spark/balance-sheet/historic/"

# Spark ALM (allocator) wallet address per chain — confirmed via BA Labs
# top-wallet aggregation on 2026-01-31.
SPARK_ALM = {
    "ethereum":    "0x1601843c5e9bc251a3272907010afa41fa18347e",
    "base":        "0x2917956eff0b5eaf030abdb4ef4296df775009ca",
    "arbitrum":    "0x92afd6f2385a90e44da3a8b60fe36f6cbe1d8709",
    "optimism":    "0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb",
    "unichain":    "0x345e368fccd62266b3f5f37c9a131fd1c39f5869",
    "avalanche_c": "0xece6b0e8a54c2f44e066fbb9234e7157b15b7fec",
}

# Spark Savings V2 venues — BA Labs keys these by ``wallet_address`` (the
# vault), not the underlying token.
VAULT_KEYED_VENUES = {"S56", "S57", "S58", "S59", "S60"}

# S23 Anchorage — BA Labs uses ``category=anchorage`` with a synthetic
# token address (it's an off-chain Anchorage API exposure, not on-chain).
ANCHORAGE_VENUE = "S23"

# Chain normalization (our config uses ``avalanche_c``; BA Labs uses ``avalanche``).
_CHAIN_NORM = {
    "ethereum":    "ethereum",
    "base":        "base",
    "arbitrum":    "arbitrum",
    "optimism":    "optimism",
    "unichain":    "unichain",
    "avalanche_c": "avalanche",
}


def _fetch_ba_labs() -> list[dict]:
    cached = Path("/tmp/ba_spark.json")
    if cached.exists():
        import time
        age_sec = time.time() - cached.stat().st_mtime
        if age_sec < 3600:  # 1h freshness window
            with cached.open() as f:
                return json.load(f)["data"]
        print(
            f"WARN: /tmp/ba_spark.json is {int(age_sec/60)} min old — "
            f"refetching from {BA_LABS_URL}",
            file=sys.stderr,
        )
    req = urllib.request.Request(BA_LABS_URL)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode()
    cached.write_text(body)
    return json.loads(body)["data"]


def _load_spark_config() -> dict:
    with (REPO / "config" / "spark.yaml").open() as f:
        return yaml.safe_load(f)


def _load_provenance(month: str) -> dict | None:
    p = REPO / "settlements" / "spark" / month / "provenance.json"
    if not p.exists():
        return None
    with p.open() as f:
        return json.load(f)


def _ba_lookup(ba_rows: list[dict], venue: dict, date: str) -> Decimal:
    """Sum BA Labs `assets` rows matching this venue at ``date``.

    Returns the matched USD balance; 0 if no rows match.
    """
    vid = venue["id"]
    chain_raw = venue.get("chain", "")
    net = _CHAIN_NORM.get(chain_raw, chain_raw)
    addr = venue.get("token", {}).get("address", "").lower()

    if vid == ANCHORAGE_VENUE:
        # Match by category=anchorage on the Spark Eth ALM.
        alm = SPARK_ALM.get(chain_raw)
        matches = [
            r for r in ba_rows
            if r["date"] == date and r["what"] == "assets"
            and r.get("category") == "anchorage"
            and r.get("wallet_address", "").lower() == alm
            and r.get("network") == net
        ]
    elif vid in VAULT_KEYED_VENUES:
        # Our token.address IS the vault — match by wallet_address.
        matches = [
            r for r in ba_rows
            if r["date"] == date and r["what"] == "assets"
            and r.get("wallet_address", "").lower() == addr
            and r.get("network") == net
        ]
    else:
        # Default: match by ``(wallet=Spark ALM on chain, token=our addr)``.
        alm = SPARK_ALM.get(chain_raw)
        if alm is None:
            return Decimal("0")
        matches = [
            r for r in ba_rows
            if r["date"] == date and r["what"] == "assets"
            and r["token_address"].lower() == addr
            and r.get("wallet_address", "").lower() == alm
            and r["network"] == net
        ]
    return sum((Decimal(str(r["balance"])) for r in matches), Decimal("0"))


_MONTH_EOM = {
    "2026-01": "2026-01-31",
    "2026-02": "2026-02-28",
    "2026-03": "2026-03-31",
    "2026-04": "2026-04-30",
    "2026-05": "2026-05-31",
}


def main(argv: list[str]) -> int:
    cfg = _load_spark_config()
    venues = {v["id"]: v for v in cfg["venues"]}
    ba = _fetch_ba_labs()
    print(f"Fetched {len(ba)} BA Labs rows; {len(venues)} Spark venues in config")

    months = argv or list(_MONTH_EOM.keys())

    rows = []  # (month, venue_id, label, chain, ours, ba, diff)
    for month in months:
        eom = _MONTH_EOM.get(month, month)
        prov = _load_provenance(month)
        if prov is None:
            print(f"  WARN  no provenance.json for {month}")
            continue
        ours_by_vid = {v["venue_id"]: Decimal(v["value_eom"]) for v in prov["venue_breakdown"]}
        # Also include display_only_breakdown
        for v in prov.get("display_only_breakdown", []):
            ours_by_vid[v["venue_id"]] = Decimal(v["value_eom"])
        # Iterate over EVERY YAML venue so missing-from-provenance venues
        # (e.g. S56–S60 skipped by the unimplemented S2 compute path) still
        # appear in the diff report.
        for vid, v in venues.items():
            ours = ours_by_vid.get(vid, Decimal("0"))
            ba_val = _ba_lookup(ba, v, eom)
            diff = ours - ba_val
            # ``missing_in_ours`` flag for downstream filtering / annotation.
            in_ours = vid in ours_by_vid
            rows.append((month, vid, v["label"], v["chain"], ours, ba_val, diff))

    # Largest absolute discrepancies
    print("\n=== Top 30 |diff| across all (venue × month) ===")
    print(f"{'month':<10} {'venue':<6} {'chain':<14} {'ours':>17} {'ba':>17} {'diff':>17}  label")
    print("-" * 130)
    for month, vid, label, chain, ours, ba_val, diff in sorted(rows, key=lambda r: -abs(r[6]))[:30]:
        print(f"{month:<10} {vid:<6} {chain:<14} ${float(ours):>16,.2f} ${float(ba_val):>16,.2f} ${float(diff):>16,.2f}  {label[:60]}")

    # Per-month summary: how much total |diff|?
    print("\n=== Per-month total |diff| ===")
    by_month = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0)])
    for month, vid, label, chain, ours, ba_val, diff in rows:
        by_month[month][0] += ours
        by_month[month][1] += ba_val
        by_month[month][2] += abs(diff)
    for month in sorted(by_month.keys()):
        t = by_month[month]
        print(f"  {month}:  ours_total=${float(t[0]):>16,.2f}  ba_total=${float(t[1]):>16,.2f}  sum_abs_diff=${float(t[2]):>16,.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
