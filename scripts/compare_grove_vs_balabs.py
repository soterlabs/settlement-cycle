"""Compare our Grove settlements vs BA Labs balance-sheet API per-venue.

Mirrors ``scripts/compare_spark_vs_balabs.py`` for the Grove prime.

Method: per-venue ``value_eom`` from our ``provenance.json`` vs BA Labs
``assets`` rows matched by
``(wallet=Grove ALM-on-chain OR holder_override, token=our.token.address)``.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
BA_LABS_URL = "https://observatory.data.blockanalitica.com/primes/grove/balance-sheet/historic/"

# Grove ALM (main holder) per chain — from config/grove.yaml addresses.<chain>.alm.
GROVE_ALM = {
    "ethereum":    "0x491edfb0b8b608044e227225c715981a30f3a44e",
    "base":        "0x9b746dbc5269e1df6e4193bcb441c0fbbf1cecee",
    "avalanche_c": "0x7107dd8f56642327945294a18a4280c78e153644",
    "plume":       "0x1db91ad50446a671e2231f77e00948e68876f812",
    "monad":       "0x94b398acb2fce988871218221ea6a4a2b26cccbc",
}

# Chain normalization (our config uses ``avalanche_c``; BA Labs uses ``avalanche``).
_CHAIN_NORM = {
    "ethereum":    "ethereum",
    "base":        "base",
    "arbitrum":    "arbitrum",
    "optimism":    "optimism",
    "unichain":    "unichain",
    "avalanche_c": "avalanche",
    "plume":       "plume",
    "monad":       "monad",
}

_MONTH_EOM = {
    "2026-01": "2026-01-31",
    "2026-02": "2026-02-28",
    "2026-03": "2026-03-31",
    "2026-04": "2026-04-30",
    "2026-05": "2026-05-31",
}


def _fetch_ba_labs() -> list[dict]:
    cached = Path("/tmp/ba_grove.json")
    if cached.exists():
        age_sec = time.time() - cached.stat().st_mtime
        if age_sec < 3600:
            with cached.open() as f:
                return json.load(f)["data"]
        print(
            f"WARN: /tmp/ba_grove.json is {int(age_sec/60)} min old — refetching",
            file=sys.stderr,
        )
    req = urllib.request.Request(BA_LABS_URL)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode()
    cached.write_text(body)
    return json.loads(body)["data"]


def _load_grove_config() -> dict:
    with (REPO / "config" / "grove.yaml").open() as f:
        return yaml.safe_load(f)


def _load_provenance(month: str) -> dict | None:
    p = REPO / "settlements" / "grove" / month / "provenance.json"
    if not p.exists():
        return None
    with p.open() as f:
        return json.load(f)


def _ba_lookup(ba_rows: list[dict], venue: dict, date: str) -> Decimal:
    """Sum BA Labs `assets` rows matching this venue at ``date``.

    Default match: ``(token=venue.token.address, wallet=Grove ALM-on-chain
    OR venue.holder_override, network=venue.chain)``.
    """
    addr = venue.get("token", {}).get("address", "").lower()
    chain_raw = venue.get("chain", "")
    net = _CHAIN_NORM.get(chain_raw, chain_raw)
    holder = venue.get("holder_override", GROVE_ALM.get(chain_raw, "")).lower()
    matches = [
        r for r in ba_rows
        if r["date"] == date and r["what"] == "assets"
        and r["token_address"].lower() == addr
        and r.get("wallet_address", "").lower() == holder
        and r["network"] == net
    ]
    return sum((Decimal(str(r["balance"])) for r in matches), Decimal("0"))


def main(argv: list[str]) -> int:
    cfg = _load_grove_config()
    venues = {v["id"]: v for v in cfg["venues"]}
    ba = _fetch_ba_labs()
    print(f"Fetched {len(ba)} BA Labs rows; {len(venues)} Grove venues in config")

    # Display-only venues are tracked for reporting but EXCLUDED from MSC
    # totals (revenue / CoF), so they don't belong in the BA Labs settlement-
    # parity check by default. Pass ``--include-display-only`` to surface
    # them anyway (useful when verifying that the off-chain ↔ on-chain
    # relays are still tracking right).
    include_display = "--include-display-only" in argv
    argv = [a for a in argv if a != "--include-display-only"]
    display_only_ids = {v["id"] for v in cfg["venues"] if v.get("display_only")}

    months = argv or list(_MONTH_EOM.keys())

    rows = []  # (month, vid, label, chain, ours, ba, diff)
    for month in months:
        eom = _MONTH_EOM.get(month, month)
        prov = _load_provenance(month)
        if prov is None:
            print(f"  WARN  no provenance.json for {month}")
            continue
        ours_by_vid = {v["venue_id"]: Decimal(v["value_eom"]) for v in prov["venue_breakdown"]}
        for v in prov.get("display_only_breakdown", []):
            ours_by_vid[v["venue_id"]] = Decimal(v["value_eom"])
        for vid, v in venues.items():
            if vid in display_only_ids and not include_display:
                continue
            ours = ours_by_vid.get(vid, Decimal("0"))
            ba_val = _ba_lookup(ba, v, eom)
            diff = ours - ba_val
            rows.append((month, vid, v["label"], v["chain"], ours, ba_val, diff))

    print("\n=== Top 30 |diff| across all (venue × month) ===")
    print(f"{'month':<10} {'venue':<6} {'chain':<14} {'ours':>17} {'ba':>17} {'diff':>17}  label")
    print("-" * 130)
    for month, vid, label, chain, ours, ba_val, diff in sorted(rows, key=lambda r: -abs(r[6]))[:30]:
        print(f"{month:<10} {vid:<6} {chain:<14} ${float(ours):>16,.2f} ${float(ba_val):>16,.2f} ${float(diff):>16,.2f}  {label[:60]}")

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
