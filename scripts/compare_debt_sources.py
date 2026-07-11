#!/usr/bin/env python3
"""Side-by-side comparison of the Dune and Envio ``IDebtSource`` implementations.

The migration plan is "live with both, retire Dune only when they match". This
script is that check. It runs both sources over the *same* ``(ilk, start, pin)``
and diffs the daily debt series they return.

    # Resolve the EoM pin block for the month via RPC, then compare:
    python scripts/compare_debt_sources.py --prime spark --month 2026-06

    # Skip RPC block resolution — supply the pin block yourself:
    python scripts/compare_debt_sources.py --prime spark --pin-block 24971074

    # Also compare the rate-scaled output that actually feeds Compute
    # (adds ~28 RPC calls for the per-day ilk.rate reads):
    python scripts/compare_debt_sources.py --prime spark --month 2026-06 --full

Exit code is 0 when every day matches within ``--tol`` (default: exact), 1
otherwise — so it drops straight into CI as the gate that unlocks Dune removal.

Env: both sources read their own credentials — ``DUNE_API_KEY`` for Dune,
``ENVIO_GRAPHQL_URL`` (+ optional token/secret) for Envio, plus ``ETH_RPC`` for
pin-block / rate resolution.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd

from settle.domain import Chain, Month, Period
from settle.domain.config import load_prime_by_id
from settle.normalize.debt import get_debt_timeseries
from settle.normalize.registry import get_block_resolver, get_debt_source


def _resolve_pin_block(month: Month) -> int:
    resolver = get_block_resolver("rpc")
    eod = datetime.combine(month.last_day, datetime.max.time(), tzinfo=timezone.utc)
    return resolver.block_at_or_before(Chain.ETHEREUM.value, eod)


def _merge(dune: pd.DataFrame, envio: pd.DataFrame) -> pd.DataFrame:
    d = dune.rename(columns={"daily_dart": "dune_daily", "cum_debt": "dune_cum"})
    e = envio.rename(columns={"daily_dart": "envio_daily", "cum_debt": "envio_cum"})
    merged = pd.merge(d, e, on="block_date", how="outer").sort_values("block_date")
    for col in ("dune_daily", "dune_cum", "envio_daily", "envio_cum"):
        merged[col] = merged[col].apply(
            lambda v: Decimal(0) if (v is None or (isinstance(v, float) and pd.isna(v))) else v
        )
    merged["daily_diff"] = merged["envio_daily"] - merged["dune_daily"]
    merged["cum_diff"] = merged["envio_cum"] - merged["dune_cum"]
    return merged.reset_index(drop=True)


def _report(merged: pd.DataFrame, tol: Decimal, label: str) -> bool:
    mism = merged[merged["cum_diff"].apply(lambda v: abs(v) > tol)]
    max_cum = max((abs(v) for v in merged["cum_diff"]), default=Decimal(0))
    max_daily = max((abs(v) for v in merged["daily_diff"]), default=Decimal(0))

    print(f"\n=== {label} — {len(merged)} days ===")
    print(f"  max |daily_dart diff|: {max_daily}")
    print(f"  max |cum_debt diff|:   {max_cum}")
    print(f"  tolerance:             {tol}")

    if mism.empty:
        print(f"  ✅ MATCH — all {len(merged)} days within tolerance")
        return True

    print(f"  ❌ MISMATCH on {len(mism)} day(s):")
    print(f"    {'date':<12}{'dune_cum':>22}{'envio_cum':>22}{'cum_diff':>22}")
    for _, r in mism.head(30).iterrows():
        print(
            f"    {str(r['block_date']):<12}"
            f"{str(r['dune_cum']):>22}{str(r['envio_cum']):>22}{str(r['cum_diff']):>22}"
        )
    if len(mism) > 30:
        print(f"    ... and {len(mism) - 30} more")
    return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--prime", required=True, help="Prime id (e.g. 'spark')")
    p.add_argument("--month", help="Settlement month YYYY-MM (resolves pin block via RPC)")
    p.add_argument("--pin-block", type=int, help="Ethereum pin block (skips RPC resolution)")
    p.add_argument("--envio-source", default="hypersync", choices=["hypersync", "envio"],
                   help="Envio-side source to diff against Dune: 'hypersync' (raw-log, "
                        "recommended) or 'envio' (HyperIndex GraphQL; blocked for the "
                        "Vat's anonymous LogNote — see enviodev/hyperindex#990). Default: hypersync")
    p.add_argument("--tol", default="0", help="Absolute cum_debt tolerance in wad-USDS (default: 0 = exact)")
    p.add_argument("--full", action="store_true",
                   help="Also compare rate-scaled get_debt_timeseries (adds ~28 RPC rate reads)")
    args = p.parse_args(argv)

    if not args.month and not args.pin_block:
        p.error("pass --month (RPC-resolve pin) or --pin-block")

    prime = load_prime_by_id(args.prime)
    if prime.ilk_bytes32 is None:
        print(f"Prime {prime.id!r} has no ilk (agent-rate-only) — no debt to compare.")
        return 0

    pin = args.pin_block or _resolve_pin_block(Month.parse(args.month))
    tol = Decimal(args.tol)
    print(f"prime={prime.id}  ilk=0x{prime.ilk_bytes32.hex()}  "
          f"start={prime.start_date}  pin_block={pin}")

    dune_src = get_debt_source("dune")
    envio_src = get_debt_source(args.envio_source)
    print(f"comparing dune  vs  {args.envio_source}")

    # --- Primary comparison: raw IDebtSource output (the exact Dune→Envio swap).
    dune_raw = dune_src.debt_timeseries(prime.ilk_bytes32, prime.start_date, pin)
    envio_raw = envio_src.debt_timeseries(prime.ilk_bytes32, prime.start_date, pin)
    ok = _report(_merge(dune_raw, envio_raw), tol, "IDebtSource.debt_timeseries (normalised Art, wad)")

    # --- Optional: full rate-scaled series (the number Compute actually consumes).
    if args.full and args.month:
        period = Period.from_month(Month.parse(args.month), pin_blocks={Chain.ETHEREUM: pin})
        resolver = get_block_resolver("rpc")
        dune_full = get_debt_timeseries(prime, period, source=dune_src, block_resolver=resolver)
        envio_full = get_debt_timeseries(prime, period, source=envio_src, block_resolver=resolver)
        ok = _report(_merge(dune_full, envio_full), tol,
                     "get_debt_timeseries (rate-scaled USDS, period only)") and ok

    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
