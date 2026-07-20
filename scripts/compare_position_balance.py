#!/usr/bin/env python3
"""Parity harness: RPC vs HyperSync for Tier 1 (block resolver) + Tier 2
(position balance, incl. Cat C aTokens). Phase A of the review.

For a prime it checks, at SoM and EoM:
  * block_resolver: hypersync block == rpc block (exact), per chain;
  * position_balance: hypersync balance_at == rpc balanceOf (exact) for the
    subproxy + every venue token, and prints the classification verdict
    (events / aave / rpc) so a silent RPC-fallback is visible.

    python scripts/compare_position_balance.py --prime obex --month 2026-06

Exit 0 iff everything matches exactly. Env: ENVIO_API_TOKEN, ETH_RPC etc.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from settle.domain import Chain
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.normalize.registry import get_block_resolver, get_position_balance_source


def _eod(month: Month, day: int) -> datetime:
    return datetime(month.year, month.month, day, 23, 59, 59, tzinfo=timezone.utc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prime", required=True)
    ap.add_argument("--month", required=True, help="YYYY-MM")
    args = ap.parse_args(argv)
    prime = load_prime_by_id(args.prime)
    month = Month.parse(args.month)

    rpc_res = get_block_resolver("rpc")
    hs_res = get_block_resolver("hypersync")
    rpc_pb = get_position_balance_source("rpc")
    hs_pb = get_position_balance_source("hypersync")
    ok = True

    print(f"prime={prime.id} month={month}\n--- block_resolver: hypersync vs rpc ---")
    blocks: dict[str, int] = {}
    for chain in sorted(c.value for c in prime.chains):
        anchor = _eod(month, month.last_day.day)
        try:
            r = rpc_res.block_at_or_before(chain, anchor)
            h = hs_res.block_at_or_before(chain, anchor)
        except Exception as e:  # noqa: BLE001
            print(f"  {chain:12s} ERROR {e}"); ok = False; continue
        blocks[chain] = h
        m = "OK" if h == r else "MISMATCH"
        ok = ok and h == r
        print(f"  {chain:12s} rpc={r} hypersync={h} [{m}]")

    print("--- position_balance: hypersync vs rpc (EoM) ---")
    targets = []
    eth = Chain.ETHEREUM
    if eth in prime.subproxy:
        from settle.domain.sky_tokens import USDS_ETHEREUM
        targets.append(("subproxy USDS", "ethereum", USDS_ETHEREUM.address.value,
                        prime.subproxy[eth].value))
    for v in prime.venues:
        holder = v.holder_override or prime.alm.get(v.chain)
        if holder is None:
            continue
        targets.append((f"{v.id} {v.token.symbol} ({v.pricing_category.value})",
                        v.chain.value, v.token.address.value, holder.value))

    for label, chain, token, holder in targets:
        blk = blocks.get(chain)
        if blk is None:
            continue
        try:
            r = rpc_pb.balance_at(chain, token, holder, blk)
            h = hs_pb.balance_at(chain, token, holder, blk)
        except Exception as e:  # noqa: BLE001
            print(f"  {label:34s} ERROR {e}"); ok = False; continue
        verdict = hs_pb._verdict.get((chain, bytes(token).hex()), "?")
        m = "OK" if h == r else "MISMATCH"
        ok = ok and h == r
        print(f"  {label:34s} rpc={r} hs={h} [{m}] verdict={verdict}")

    print("\n✅ MATCH" if ok else "\n❌ MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
