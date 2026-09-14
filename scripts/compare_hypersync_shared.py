"""Compare shared idle balances and Dune daily block boundaries with HyperSync."""

import argparse
import json
from datetime import UTC, datetime, time
from decimal import Decimal
from pathlib import Path

import pandas as pd
from compare_hypersync_venue import compare_with_raw_precision_check

from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.domain.primes import Chain
from settle.domain.sky_tokens import USDS_BY_CHAIN, USDS_ETHEREUM, sUSDS_ETHEREUM
from settle.extract import hypersync
from settle.extract.dune import execute_query
from settle.normalize.sources._paths import QUERIES_DIR
from settle.normalize.sources.dune_balances import DuneBalanceSource
from settle.normalize.sources.hypersync_balances import HyperSyncBalanceSource


def anchor(day):
    return int(datetime.combine(day, time(23, 59, 59), UTC).timestamp())


def blocks(chain, month):
    pin = hypersync.find_block_at_or_before(chain, anchor(month.last_day))
    df = execute_query(QUERIES_DIR / "blocks_at_eod.sql", {
        "chain": chain, "start_date": str(month.first_day), "end_date": str(month.last_day),
    }, pin)
    checks = []
    for row in df.to_dict("records"):
        day, block = pd.Timestamp(row["block_date"]).date(), int(row["block_number"])
        # Certify every Dune boundary independently: the selected block is
        # before EoD and its immediate successor is after it. This is the
        # exact binary-search predicate, with fewer redundant head probes.
        before = hypersync.block_timestamp(chain, block)
        after = hypersync.block_timestamp(chain, block + 1)
        check = {"day": str(day), "dune_block": block, "hypersync_timestamp": before,
                 "hypersync_successor_timestamp": after, "matched": before <= anchor(day) < after}
        if day.day in {1, 15, month.last_day.day}:
            actual = hypersync.find_block_at_or_before(chain, anchor(day))
            check.update(hypersync_search_block=actual, matched=check["matched"] and actual == block)
        checks.append(check)
    return {"chain": chain, "month": str(month), "pin_block": pin,
            "oracle": "blocks_at_eod.sql", "checks": checks,
            "matched": len(checks) == month.last_day.day and all(c["matched"] for c in checks)}


def idle(prime_id, month):
    prime = load_prime_by_id(prime_id)
    subjects = [("ALM USDS", c, USDS_BY_CHAIN[c], holder) for c, holder in prime.alm.items()
                if c in USDS_BY_CHAIN]
    if Chain.ETHEREUM in prime.subproxy:
        subjects += [("SubProxy " + t.symbol, Chain.ETHEREUM, t, prime.subproxy[Chain.ETHEREUM])
                     for t in [USDS_ETHEREUM, sUSDS_ETHEREUM]]
    checks, pins = [], {}
    for label, chain, token, holder in subjects:
        if chain.value not in pins:
            pins[chain.value] = hypersync.find_block_at_or_before(chain.value, anchor(month.last_day))
        args = (chain.value, token.address.value, holder.value, prime.start_date, pins[chain.value])
        dune = DuneBalanceSource().cumulative_balance_timeseries(*args)
        hs = HyperSyncBalanceSource(decimals_of=lambda *a, decimals=token.decimals: decimals).cumulative_balance_timeseries(*args)
        check = compare_with_raw_precision_check(label, dune, hs, ["block_date"], ["daily_net", "cum_balance"], Decimal("0.000001"),
                    raw_sql="transfer_timeseries_raw_parity.sql", raw_params={
                        "chain": chain.value, "token": token.address.value, "holder": holder.value,
                        "start_date": str(prime.start_date), "min_transfer_raw": 0},
                    pin_block=pins[chain.value], decimals=token.decimals)
        check.update(chain=chain.value, token="0x" + token.address.value.hex(), holder="0x" + holder.value.hex())
        checks.append(check)
        print(prime_id, chain, label, check["matched"], check["max_abs_difference"], flush=True)
    return {"prime": prime_id, "month": str(month), "start": str(prime.start_date), "pins": pins,
            "checks": checks, "matched": bool(checks) and all(c["matched"] for c in checks)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prime")
    group.add_argument("--chain")
    parser.add_argument("--month", default="2026-08")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    month = Month.parse(args.month)
    report = idle(args.prime, month) if args.prime else blocks(args.chain, month)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, default=str, indent=2) + "\n")
    return 0 if report["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
