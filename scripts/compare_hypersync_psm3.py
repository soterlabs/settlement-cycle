"""Exact event-history parity for a configured PSM3, plus pinned RPC checks."""

import argparse
import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from itertools import zip_longest
from pathlib import Path

import pandas as pd

from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.domain.primes import Chain
from settle.domain.sky_tokens import PSM3_LEG_TOKENS
from settle.extract import hypersync, rpc
from settle.extract._keccak import keccak256
from settle.extract.dune import execute_query
from settle.normalize.sources._paths import QUERIES_DIR
from settle.normalize.sources.dune_psm3 import DunePsm3Source
from settle.normalize.sources.hypersync_psm3 import HyperSyncPsm3Source


def compare_histories(label, dune, hypersync):
    """Keep evidence compact while checking every raw integer in order."""
    def digest(rows):
        return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
    differences = [{"index": i, "dune": d, "hypersync": h}
                   for i, (d, h) in enumerate(zip_longest(dune, hypersync)) if d != h]
    return {"input": label, "matched": not differences,
            "dune_rows": len(dune), "hypersync_rows": len(hypersync),
            "dune_sha256": digest(dune), "hypersync_sha256": digest(hypersync),
            "mismatch_count": len(differences), "mismatches": differences[:10]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chain", required=True)
    ap.add_argument("--month", default="2026-08")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    prime, chain, month = load_prime_by_id("spark"), Chain(args.chain), Month.parse(args.month)
    psm, holder = prime.psm[chain].address, prime.alm[chain]
    pin = hypersync.find_block_at_or_before(chain.value, int(datetime.combine(month.last_day, time(23, 59, 59), UTC).timestamp()))
    d, h = DunePsm3Source(), HyperSyncPsm3Source()
    print("Dune PSM3 preload", chain, pin, flush=True)
    d._load_holder_history(chain.value, holder.value, pin_block=pin)
    d._load_pool_history(chain.value, pin_block=pin)
    print("HyperSync PSM3 preload", chain, pin, flush=True)
    h.preload(chain.value, holder.value, pin_block=pin, psm3=psm.value)
    checks = []
    for label, dv, hv in [
        ("holder shares", d._load_holder_history(chain.value, holder.value, pin_block=pin), h._load_holder_history(chain.value, holder.value, pin_block=pin)),
        ("total shares", d._load_pool_history(chain.value, pin_block=pin), h._load_pool_history(chain.value, pin_block=pin)),
    ]:
        checks.append(compare_histories(label, dv, hv))
    # Preserve the original SQL running sum, but export daily closing states
    # instead of millions of arbitrage event rows. Every valuation day plus
    # the opening anchor is compared, not just the month-end total.
    tokens = PSM3_LEG_TOKENS[chain]
    params = {"psm3": psm.value, "usdc": tokens["USDC"].address.value,
              "usds": tokens["USDS"].address.value, "susds": tokens["sUSDS"].address.value,
              "start_month": "2024-01-01"}
    daily = execute_query(QUERIES_DIR / f"psm3_reserves_daily_{chain.value}.sql", params, pin)
    states = {}
    for row in daily.to_dict("records"):
        token = row["token"]
        key = "0x" + token.hex() if isinstance(token, bytes) else str(token).lower()
        states.setdefault(key, []).append((pd.Timestamp(row["block_date"]).date(), int(Decimal(str(row["cum_balance_raw"])))))
    days = [month.first_day - timedelta(days=1) + timedelta(days=i)
            for i in range((month.last_day - month.first_day).days + 2)]
    day_blocks = {day: hypersync.find_block_at_or_before(chain.value, int(datetime.combine(day, time(23, 59, 59), UTC).timestamp())) for day in days}
    for token in tokens.values():
        key = "0x" + token.address.value.hex()
        history = sorted(states.get(key, []))
        pairs = []
        for day in days:
            dv = next((val for dt, val in reversed(history) if dt <= day), 0)
            hv = h.pool_reserve_at(chain.value, token.address.value, psm.value, day_blocks[day], decimals=token.decimals)
            pairs.append({"day": str(day), "block": day_blocks[day], "dune": dv, "hypersync": hv})
        actual = rpc.balance_of(chain, token.address, psm, pin)
        checks.append({"input": token.symbol + " daily reserves", "matched": all(x["dune"] == x["hypersync"] for x in pairs) and pairs[-1]["hypersync"] == actual,
                       "oracle": f"psm3_reserves_daily_{chain.value}.sql", "rpc_eom": actual, "days": pairs})
    for label, data, actual in [
        ("RPC shares", h._load_holder_history(chain.value, holder.value, pin_block=pin), rpc.psm3_shares(chain, psm, holder, pin)),
        ("RPC totalShares", h._load_pool_history(chain.value, pin_block=pin),
         int(rpc.eth_call(chain, psm, "0x" + keccak256(b"totalShares()")[:4].hex(), pin), 16)),
    ]:
        value = data[-1][1] if data else 0
        checks.append({"input": label, "matched": value == actual, "hypersync": value, "rpc": actual})
    report = {"prime": "spark", "venue": "PSM3-" + args.chain, "month": args.month,
              "pin_block": pin, "contract": "0x" + psm.value.hex(), "holder": "0x" + holder.value.hex(),
              "matched": all(c["matched"] for c in checks), "checks": checks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, default=str, indent=2) + "\n")
    print([(c["input"], c["matched"]) for c in checks], flush=True)
    return 0 if report["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
