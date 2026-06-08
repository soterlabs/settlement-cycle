"""Extend Grove fixture sets with E37 (Maple syrupUSDC) and E38 (Agora
incentives) data, captured via Dune at each fixture's existing pin block.

Adds to each of:
  * ``tests/fixtures/grove_2026_03/dune_outputs.json``  (Q1: Jan/Feb/Mar 2026)
  * ``tests/fixtures/grove_2026_04/dune_outputs.json``  (Apr 2026)
  * ``tests/fixtures/grove_2026_05/dune_outputs.json``  (May 2026)

Per fixture, four new keys:
  * ``vault_e37_mints``      — syrupUSDC Transfers from ZERO to Grove ALM
  * ``vault_e37_burns``      — syrupUSDC Transfers from Grove ALM to ZERO
  * ``cash_dist_e38_p0``     — AUSD Transfers from Agora payer #0 to Grove ALM
  * ``cash_dist_e38_p1``     — AUSD Transfers from Agora payer #1 to Grove ALM

E37 fixtures are empty for grove_2026_03 / _04 (position only opens in
May), but we capture them anyway so the loader sees a deterministic
empty frame instead of a missing key.

Usage:
    DUNE_API_KEY=... python3 scripts/extend_grove_fixtures_e37_e38.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from settle.extract.dune import execute_query  # noqa: E402

QUERIES = REPO / "src" / "settle" / "queries"

# Each fixture set: (dir_name, pin_block_ethereum)
# Pin blocks copy the existing values in each capture script.
FIXTURES = [
    ("grove_2026_03", 24781026),  # Mar 31 2026 23:59 UTC (Q1 fixture, pin = Mar EoM)
    ("grove_2026_04", 24996367),  # Apr 30 2026 23:59 UTC
    ("grove_2026_05", 25218797),  # May 31 2026 23:59 UTC
]

GROVE_ALM_ETH = bytes.fromhex("491edfb0b8b608044e227225c715981a30f3a44e")
ZERO_ADDR = b"\x00" * 20

# E37 syrupUSDC token
SYRUP_USDC = bytes.fromhex("80ac24aa929eaf5013f6436cda2a7ba190f5cc0b")
# E38 AUSD token + Agora payer addresses (verified on-chain).
AUSD = bytes.fromhex("00000000efe302beaa2b3e6e1b18d08d69a9012a")
AGORA_PAYER_0 = bytes.fromhex("4a4593c5d963473a95f0762bd6df4571542af651")  # Feb-Mar 2026
AGORA_PAYER_1 = bytes.fromhex("df27ac19cb1da767e181748aaa54e1535aaa3a1d")  # Apr-May 2026

START_DATE = "2025-05-14"  # Grove prime start (matches existing captures)


def _rows(df):
    out = []
    for _, r in df.iterrows():
        row = {}
        for col in df.columns:
            val = r[col]
            row[col] = val.isoformat() if hasattr(val, "isoformat") else str(val)
        out.append(row)
    return out


def venue_inflow(chain: str, token: bytes, from_addr: bytes, to_addr: bytes, pin_block: int):
    df = execute_query(
        QUERIES / "venue_inflow.sql",
        params={
            "chain": chain, "token": token,
            "from_addr": from_addr, "to_addr": to_addr,
            "start_date": START_DATE,
        },
        pin_block=pin_block,
    )
    return _rows(df)


def main() -> int:
    for fixture_dir, eth_eom in FIXTURES:
        out_path = REPO / "tests" / "fixtures" / fixture_dir / "dune_outputs.json"
        print(f"\n=== {fixture_dir} (Eth pin = {eth_eom}) ===")
        with open(out_path) as f:
            fx = json.load(f)

        # E37 — syrupUSDC mints/burns at Grove ALM
        print(f"  vault_e37_mints (syrupUSDC, 0x→ALM)…")
        fx["vault_e37_mints"] = {
            "_token": "0x" + SYRUP_USDC.hex(),
            "_about": "syrupUSDC Transfers from 0x0 to Grove Eth ALM (Cat B mints)",
            "rows": venue_inflow("ethereum", SYRUP_USDC, ZERO_ADDR, GROVE_ALM_ETH, eth_eom),
        }
        print(f"    → {len(fx['vault_e37_mints']['rows'])} rows")

        print(f"  vault_e37_burns (syrupUSDC, ALM→0x)…")
        fx["vault_e37_burns"] = {
            "_token": "0x" + SYRUP_USDC.hex(),
            "_about": "syrupUSDC Transfers from Grove Eth ALM to 0x0 (Cat B burns)",
            "rows": venue_inflow("ethereum", SYRUP_USDC, GROVE_ALM_ETH, ZERO_ADDR, eth_eom),
        }
        print(f"    → {len(fx['vault_e37_burns']['rows'])} rows")

        # E38 — Agora cash distributions (one fixture per payer)
        for i, payer in enumerate([AGORA_PAYER_0, AGORA_PAYER_1]):
            key = f"cash_dist_e38_p{i}"
            print(f"  {key} (AUSD, Agora payer {i} → ALM)…")
            fx[key] = {
                "_token": "0x" + AUSD.hex(),
                "_from":  "0x" + payer.hex(),
                "_to":    "0x" + GROVE_ALM_ETH.hex(),
                "_about": f"Agora AUSD payer #{i} → Grove Eth ALM (cash_dist)",
                "rows": venue_inflow("ethereum", AUSD, payer, GROVE_ALM_ETH, eth_eom),
            }
            print(f"    → {len(fx[key]['rows'])} rows")

        with open(out_path, "w") as f:
            json.dump(fx, f, indent=2)
        print(f"  wrote {out_path}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
