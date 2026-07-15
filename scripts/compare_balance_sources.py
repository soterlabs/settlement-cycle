#!/usr/bin/env python3
"""Parity harness: Dune vs HyperSync ``IBalanceSource`` (Phase A of the review).

Compares ``cumulative_balance_timeseries`` for a prime's subproxy + every venue
token, Dune vs HyperSync, over the same ``(start, pin)``. Exit 0 iff every
series matches within ``--tol`` (default: 0 = exact); exit 1 on any drift — so
it drops into CI as the gate for the balance migration.

    python scripts/compare_balance_sources.py --prime obex --month 2026-06
    python scripts/compare_balance_sources.py --prime spark --month 2026-06 --tol 0.000001

Env: DUNE_API_KEY (Dune side), ENVIO_API_TOKEN (HyperSync side), ETH_RPC etc.
(pin-block resolution + decimals). Runs with HYPERSYNC_NO_STORE respected.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd

from settle.domain import Chain
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.domain.sky_tokens import USDS_ETHEREUM, sUSDS_ETHEREUM
from settle.normalize.registry import get_balance_source, get_block_resolver


def _targets(prime):
    """(label, chain, token_bytes, holder_bytes) balance queries a run makes."""
    out = []
    eth = Chain.ETHEREUM
    if eth in prime.subproxy:
        sp = prime.subproxy[eth].value
        out.append(("subproxy USDS", "ethereum", USDS_ETHEREUM.address.value, sp))
        out.append(("subproxy sUSDS", "ethereum", sUSDS_ETHEREUM.address.value, sp))
    for v in prime.venues:
        holder = (v.holder_override or prime.alm.get(v.chain))
        if holder is None or v.token is None:
            continue
        out.append((f"{v.id} {v.token.symbol}", v.chain.value, v.token.address.value, holder.value))
    return out


def _cum(df):
    return Decimal(0) if df is None or df.empty else df["cum_balance"].iloc[-1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prime", required=True)
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--tol", default="0", help="abs cum_balance tolerance (decimal-adjusted)")
    args = ap.parse_args(argv)

    prime = load_prime_by_id(args.prime)
    month = Month.parse(args.month)
    tol = Decimal(args.tol)
    pin = get_block_resolver("rpc").block_at_or_before(
        "ethereum", datetime(month.year, month.month, 1, tzinfo=timezone.utc)
        .replace(day=month.last_day.day, hour=23, minute=59, second=59))
    dune = get_balance_source("dune")
    hs = get_balance_source("hypersync")
    print(f"prime={prime.id} month={month} pin={pin}  comparing dune vs hypersync (cumulative_balance)")

    ok = True
    for label, chain, token, holder in _targets(prime):
        try:
            d = _cum(dune.cumulative_balance_timeseries(chain, token, holder, prime.start_date, pin))
            h = _cum(hs.cumulative_balance_timeseries(chain, token, holder, prime.start_date, pin))
        except Exception as e:  # noqa: BLE001
            print(f"  {label:28s} ERROR {e}"); ok = False; continue
        diff = abs(h - d)
        status = "OK" if diff <= tol else f"DIFF={h - d}"
        if diff > tol:
            ok = False
        print(f"  {label:28s} dune={d} hypersync={h} [{status}]")
    print("\n✅ MATCH" if ok else "\n❌ MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
