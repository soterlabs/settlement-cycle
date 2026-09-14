"""Exact event-history parity for a configured PSM3, plus pinned RPC checks."""

import argparse
import json
from datetime import UTC, datetime, time
from pathlib import Path

from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.domain.primes import Chain
from settle.domain.sky_tokens import PSM3_LEG_TOKENS
from settle.extract import hypersync, rpc
from settle.extract._keccak import keccak256
from settle.normalize.sources.dune_psm3 import DunePsm3Source
from settle.normalize.sources.hypersync_psm3 import HyperSyncPsm3Source


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
    d.preload(chain.value, holder.value, pin_block=pin, psm3=psm.value)
    print("HyperSync PSM3 preload", chain, pin, flush=True)
    h.preload(chain.value, holder.value, pin_block=pin, psm3=psm.value)
    checks = []
    for label, dv, hv in [
        ("holder shares", d._load_holder_history(chain.value, holder.value, pin_block=pin), h._load_holder_history(chain.value, holder.value, pin_block=pin)),
        ("total shares", d._load_pool_history(chain.value, pin_block=pin), h._load_pool_history(chain.value, pin_block=pin)),
    ]:
        checks.append({"input": label, "matched": dv == hv, "dune_rows": len(dv), "hypersync_rows": len(hv),
                       "dune": dv, "hypersync": hv})
    dr, hr = (s._load_reserves_history(chain.value, psm.value, pin_block=pin) for s in (d, h))
    for token in PSM3_LEG_TOKENS[chain].values():
        key = "0x" + token.address.value.hex()
        dv, hv = dr.get(key, ([], pin))[0], hr.get(key, ([], pin))[0]
        onchain = rpc.balance_of(chain, token.address, psm, pin)
        calculated = hv[-1][1] if hv else 0
        checks.append({"input": token.symbol + " reserves", "matched": dv == hv and calculated == onchain,
                       "dune_rows": len(dv), "hypersync_rows": len(hv),
                       "hypersync_eom": calculated, "rpc_eom": onchain,
                       "first_mismatch": next(((a, b) for a, b in zip(dv, hv, strict=False) if a != b), None)})
    for label, data, actual in [
        ("RPC shares", h._load_holder_history(chain.value, holder.value, pin_block=pin), rpc.psm3_shares(chain, psm, holder, pin)),
        ("RPC totalShares", h._load_pool_history(chain.value, pin_block=pin),
         int(rpc.eth_call(chain, psm, "0x" + keccak256(b"totalShares()")[:4].hex(), pin), 16)),
    ]:
        value = data[-1][1] if data else 0
        checks.append({"input": label, "matched": value == actual, "hypersync": value, "rpc": actual})
    report = {"prime": "spark", "venue": "PSM3-" + args.chain, "month": args.month,
              "pin_block": pin, "contract": "0x" + psm.value.hex(),
              "matched": all(c["matched"] for c in checks), "checks": checks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, default=str, indent=2) + "\n")
    print([(c["input"], c["matched"]) for c in checks], flush=True)
    return 0 if report["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
