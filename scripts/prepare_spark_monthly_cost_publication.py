"""Publish reviewed Spark working costs from September onward, never settlements.

Revenue is reused from the explicitly pinned control, not recalculated. Missing
revenue (including newly traced positions) stays null. Default is a dry run.
"""

import argparse
import copy
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

from audit_saved_funding_gap import read as read_file
from report_allocation_yields import build as build_yields

from settle.revenue.allocations import (
    apply_schema,
    build_documents,
    indexed,
    month_bounds,
    publish,
    read,
)
from settle.store.db import connect


def prepare(costs, readiness, control, *, hashes):
    month = costs["month"]
    start, end = month_bounds(month)
    if costs["prime"] != "spark" or month < "2026-09":
        raise ValueError("Only Spark allocations from September 2026 onward are authorized")
    if (control["prime_id"], control["period"]["start"], control["period"]["end"]) != (
        "spark",
        str(start),
        str(end),
    ):
        raise ValueError("Revenue and borrowing-cost periods differ")
    originals = indexed(readiness["results"], "prime")
    c = copy.deepcopy(originals["spark"])
    if (c["start"], c["end"]) != (str(start), str(end)):
        raise ValueError("Capital exposure period differs")
    old = indexed(c["allocations"], "venue")
    revenues = indexed(control["venue_breakdown"], "venue_id")
    candidates = indexed(costs["allocations"], "venue")
    rows = []
    total = D(0)
    for venue, r in candidates.items():
        cost = r["borrowing_cost_usd"]
        row = copy.deepcopy(
            old.get(
                venue,
                {
                    "venue": venue,
                    "label": r["label"],
                    "status": "missing_capital_history",
                    "cost_of_funds": None,
                },
            )
        )
        row["label"] = r["label"]
        if cost is not None:
            if (
                r["status"] != "working_estimate_existing_funding_policy"
                or not D(cost).is_finite()
                or D(cost) < 0
            ):
                raise ValueError("Invalid reviewed cost estimate")
            total += D(cost)
            row["status"] = "calculable_under_assumption"
            row["cost_of_funds"] = str(cost)
            # Funding principal is not the original capital or yield NAV.
            # Keep those independent exposure fields from reviewed readiness.
        else:
            row["cost_of_funds"] = None
            if row["status"] in (
                "calculable_under_assumption",
                "excluded_from_cost",
                "no_principal_in_period",
            ):
                row["status"] = "missing_capital_history"
        row["revenue"] = revenues.get(venue, {}).get("revenue")
        rows.append(row)
    if abs(total - D(costs["venue_cost_subtotal_usd"])) > D("0.000001"):
        raise ValueError("Cost export subtotal differs")
    c["allocations"] = rows
    c["policy"] = (
        "Reviewed working estimate: proportional Sky/Savings funding; unresolved prime-level differences are not assigned to venues."
    )
    c["limitations"] = [*c["limitations"], *costs["limitations"]]
    ready = {"results": [c]}
    yields = build_yields(ready, {"spark": control})
    doc = build_documents(ready, yields, source_hashes=hashes)[0]
    for row in doc["allocations"]:
        r = candidates[row["venue_id"]]
        row["borrowing_cost_method"] = (
            "proportional_sky_savings" if row["borrowing_cost_usd"] is not None else None
        )
        row["average_sky_funded_principal_usd"] = r["average_sky_funded_principal_usd"]
        row["average_savings_funded_principal_usd"] = r["average_savings_funded_principal_usd"]
    doc["reconciliation"] = {
        k: v
        for k, v in costs.items()
        if k
        in (
            "acknowledged_psm3_unattributed_value_cost_equivalent_usd",
            "venue_cost_subtotal_usd",
            "separate_deduction_credits_usd",
            "subtotal_after_separate_credits_usd",
            "global_borrowing_cost_usd",
            "separate_financing_costs",
            "comparison_difference_after_financing_usd",
        )
    }
    doc["reconciliation"]["status"] = "known_limitations_not_distributed"
    return doc


def verify_estimates(costs, evidence):
    """Reject edited exports even when their grand total still matches."""
    start, end = month_bounds(costs["month"])
    if (evidence["start"], evidence["end"]) != (str(start), str(end)):
        raise ValueError("Replay evidence period differs")
    source = indexed(evidence["allocations"], "venue")
    rows = indexed(costs["allocations"], "venue")
    priced = {k for k, r in source.items() if r["conditional_sky_borrowing_cost"] is not None}
    if priced != {k for k, r in rows.items() if r["borrowing_cost_usd"] is not None}:
        raise ValueError("Reviewed replay cost coverage differs")
    for venue in priced:
        row, original = rows[venue], source[venue]
        if D(row["borrowing_cost_usd"]) != D(original["conditional_sky_borrowing_cost"]):
            raise ValueError("Exported cost differs from reviewed replay")


def publish_reviewed(conn, document, expected_revision):
    conn.execute("LOCK TABLE allocation_monthly_results IN SHARE ROW EXCLUSIVE MODE")
    current = read(conn, document["prime"], document["month"])
    actual = current["revision_id"] if current else "missing"
    if actual != expected_revision:
        raise ValueError("Monthly publication changed; review the new baseline")
    return publish(conn, document)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("costs", "readiness", "control", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--publish", action="store_true")
    p.add_argument("--expected-revision")
    a = p.parse_args()
    if a.publish and not a.expected_revision:
        p.error("Publishing requires --expected-revision (use missing for first publication)")
    paths = [a.costs, a.readiness, a.control]
    if a.output.resolve() in {p.resolve() for p in paths}:
        p.error("Output cannot overwrite inputs")
    costs = read_file(a.costs)
    # The exported costs are bound to the completed, reviewed replay, not to
    # a mutable config file. Config is retained as provenance, not re-executed.
    for raw, expected in costs["input_sha256"].items():
        path = Path(raw)
        if path.name == "spark.yaml":
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Reviewed cost source has changed")
    reconciliation_path = next(
        Path(p) for p in costs["input_sha256"] if Path(p).name == "reconciliation.json"
    )
    reconciliation = read_file(reconciliation_path)
    if (
        reconciliation["input_sha256"].get(str(a.control))
        != hashlib.sha256(a.control.read_bytes()).hexdigest()
    ):
        raise ValueError("Revenue control is not the one used to price borrowing costs")
    evidence_path = next(
        Path(p) for p in costs["input_sha256"] if Path(p).name == "conditional-costs.json.gz"
    )
    verify_estimates(costs, read_file(evidence_path))
    readiness = read_file(a.readiness)
    if (
        readiness["input_sha256"].get(str(a.control))
        != hashlib.sha256(a.control.read_bytes()).hexdigest()
    ):
        raise ValueError("Exposure and revenue control hashes differ")
    hashes = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in zip(("costs", "readiness", "control"), paths, strict=True)
    }
    document = prepare(costs, readiness, read_file(a.control), hashes=hashes)
    a.output.write_text(json.dumps(document, default=str, indent=2) + "\n")
    if a.publish:
        with connect() as conn, conn.transaction():
            apply_schema(conn)
            revision = publish_reviewed(conn, document, a.expected_revision)
        print(
            json.dumps(
                {"published": revision, "month": document["month"], "count": document["count"]}
            )
        )
    else:
        print(
            json.dumps({"publish": False, "month": document["month"], "count": document["count"]})
        )


if __name__ == "__main__":
    main()
