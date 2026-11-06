#!/usr/bin/env python3
"""Compare frozen Spark financing diagnostics against unchanged settlement controls.

Residual totals are gross inception-to-pin transaction differences, not monthly
revenue or debt. Savings matches identify cash routes without certifying funding.
"""

import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

from audit_spark_savings_funding import savings_flows, summarize


def validate_unchanged_controls(before, after, control_hash):
    if before["control_sha256"] != control_hash or after["control_sha256"] != control_hash:
        raise ValueError("Published control differs between financing snapshots")
    old, new = before["per_ilk_reconciliation"]["by_ilk"], after["per_ilk_reconciliation"]["by_ilk"]
    if old.keys() != new.keys():
        raise ValueError("Financing snapshots contain different ilks")
    for ilk in old:
        for key in ("global_cost", "msc_cost", "global_excluding_msc"):
            if D(old[ilk][key]) != D(new[ilk][key]):
                raise ValueError("Global borrowing cost or MSC exclusion changed")
    for key in ("drawn_by_ilk", "repaid_by_ilk"):
        if {k: D(v) for k, v in before[key].items()} != {k: D(v) for k, v in after[key].items()}:
            raise ValueError("Observed debt draws or repayments changed")


def stats(r, flows):
    result = {
        "eligible_allocation_cost": r["allocation_cost_of_funds"],
        "modeled_allocation_cost": str(
            sum(
                (
                    D(x["modeled_cost_of_funds"])
                    for x in r["allocations"]
                    if x.get("modeled_cost_of_funds") is not None
                ),
                D(0),
            )
        ),
        "allocation_status_counts": {},
        "per_ilk": r["per_ilk_reconciliation"]["by_ilk"],
    }
    for row in r["allocations"]:
        s = row["basis_status"]
        result["allocation_status_counts"][s] = result["allocation_status_counts"].get(s, 0) + 1
    for field in ["unmatched_receipts", "unmatched_outflows", "rounding_receipts"]:
        amounts = [D(x) for x in r[field].values()]
        result[field] = {
            "count": len(amounts),
            "total": str(sum(amounts, D(0))),
            "above_one_cent": sum(x > D(".01") for x in amounts),
            "total_above_one_cent": str(sum((x for x in amounts if x > D(".01")), D(0))),
        }
    savings = summarize(flows, r)
    result["identified_savings_cash_not_yet_modeled"] = savings["whole_transaction_matches"]
    result["largest_residuals_not_wholly_matched_to_savings"] = {}
    for field in ["unmatched_receipts", "unmatched_outflows"]:
        result["largest_residuals_not_wholly_matched_to_savings"][field] = dict(
            sorted(
                ((k, v) for k, v in r[field].items() if k not in savings["matched"][field]),
                key=lambda x: D(x[1]),
                reverse=True,
            )[:10]
        )
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("before", "after", "control", "savings-events", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    inputs = [args.before, args.after, args.control, args.savings_events]
    if args.output.resolve() in {x.resolve() for x in inputs}:
        raise ValueError("Comparison output must not overwrite evidence")
    before, after = (
        json.loads(gzip.decompress(x.read_bytes()) if x.suffix == ".gz" else x.read_bytes())
        for x in (args.before, args.after)
    )
    hashes = {str(x): hashlib.sha256(x.read_bytes()).hexdigest() for x in inputs}
    validate_unchanged_controls(before, after, hashes[str(args.control)])
    if json.loads(args.control.read_text())["prime_id"] != "spark":
        raise ValueError("This Savings comparison requires Spark controls")
    raw = args.savings_events.read_bytes()
    flows = savings_flows(
        json.loads(gzip.decompress(raw) if args.savings_events.suffix == ".gz" else raw)
    )
    result = {
        "scope": __doc__.strip(),
        "replay_commit": after["replay_commit"],
        "before": stats(before, flows),
        "after": stats(after, flows),
        "history_patch": after["history_patch"],
        "input_hashes": hashes,
        "replay_input_hashes": after["input_hashes"],
    }
    result["residual_changes"] = {}
    for field in ("unmatched_receipts", "unmatched_outflows"):
        added = {k: v for k, v in after[field].items() if k not in before[field]}
        removed = {k: v for k, v in before[field].items() if k not in after[field]}
        result["residual_changes"][field] = {
            "new_count": len(added),
            "new_total": str(sum((D(v) for v in added.values()), D(0))),
            "removed_count": len(removed),
            "removed_total": str(sum((D(v) for v in removed.values()), D(0))),
        }
    args.output.write_text(json.dumps(result, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
