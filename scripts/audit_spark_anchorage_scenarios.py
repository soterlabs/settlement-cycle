#!/usr/bin/env python3
"""Quantify unconfirmed Anchorage cash-principal scenarios, without replaying.

This measures facility principal, NOT traced Sky-funded principal. Rate-weighted
changes are an all-Sky-funding sensitivity, not a change to total Sky expense.
"""

import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

from settle.compute.spark_anchorage_correction import correct_spark_anchorage_round_trip
from settle.compute.spark_anchorage_scenarios import (
    ACCOUNT,
    RETURNS,
    SCENARIOS,
    apply_anchorage_scenario,
)
from settle.normalize.allocation_history_cache import load_history


def daily_principal(history, days):
    batches = iter(
        sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index))
    )
    pending = next(batches, None)
    balance = D(0)
    result = {}
    for day in sorted(days):
        while pending is not None and pending.day.isoformat() <= day:
            for movement in pending.movements:
                if movement.account == ACCOUNT:
                    balance = movement.value_before + movement.change
            pending = next(batches, None)
        result[day] = balance
    return result


def audit(history, control):
    rates = {
        r["date"]: D(r["daily_sky_rev"]) / D(r["utilized"]) for r in control["sky_revenue_daily"]
    }
    baseline = daily_principal(history, rates)
    scenarios = {}
    for name, principal in SCENARIOS.items():
        fixed = apply_anchorage_scenario(history, name)
        days = daily_principal(fixed, rates)
        scenarios[name] = {
            "principal_return_july": str(principal[0]),
            "interest_return_july": str(RETURNS[0][3] - principal[0]),
            "principal_return_august": str(principal[1]),
            "interest_return_august": str(RETURNS[1][3] - principal[1]),
            "facility_cash_principal_at_end": str(days[max(days)]),
            "average_facility_cash_principal": str(sum(days.values()) / len(days)),
            "all_sky_funding_facility_cost_reallocation_sensitivity": str(
                sum((baseline[d] - days[d]) * rates[d] for d in days)
            ),
            "daily_facility_cash_principal": {d: str(v) for d, v in days.items()},
        }
    return {
        "scope": "Unconfirmed splits. Cash principal only, not certified Sky-funded basis. "
        "Rate-weighted differences are the hypothetical facility cost redistribution "
        "if all returned principal were Sky-funded; global Sky borrowing costs do not change.",
        "unadjusted_facility_cash_principal_at_end": str(baseline[max(baseline)]),
        "scenarios": scenarios,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("history", "control", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if len({p.resolve() for p in (args.history, args.control, args.output)}) != 3:
        raise ValueError("Inputs and output must be distinct")
    with gzip.open(args.history, "rt") as src:
        key = json.loads(next(src))["fingerprint"]
    history = correct_spark_anchorage_round_trip(load_history(args.history, key))
    result = audit(history, json.loads(args.control.read_text()))
    result["input_hashes"] = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (
            args.history,
            args.control,
            Path(__file__),
            Path("src/settle/compute/spark_anchorage_scenarios.py"),
            Path("src/settle/compute/spark_anchorage_correction.py"),
            Path("tests/fixtures/spark_anchorage_july_correction.json.gz"),
            Path("tests/fixtures/spark_anchorage_ambiguous_returns.json"),
        )
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: {a: b for a, b in v.items() if not a.startswith("daily_")}
                for k, v in result["scenarios"].items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
