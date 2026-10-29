#!/usr/bin/env python3
"""Rank monthly reported allocation returns using existing revenue and modeled costs.

No new revenue extraction. Gross APY uses actual plus external revenue; net
uses Prime-retained venue revenue less borrowing cost, matching the existing
allocation-financing convention. Prime-level adjustments are not distributed.
"""

import argparse
import gzip
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

from settle.compute.allocation_financing import annualized_yield


def build(readiness, controls, anchorage=None):
    output = []
    for prime in readiness["results"]:
        start, end = date.fromisoformat(prime["start"]), date.fromisoformat(prime["end"])
        n = (end - start).days + 1
        control = controls[prime["prime"]]
        if (
            control["prime_id"] != prime["prime"]
            or control["period"]["start"] != str(start)
            or control["period"]["end"] != str(end)
        ):
            raise ValueError("Yield inputs cover different periods/primes")
        revenues = {r["venue_id"]: r for r in control["venue_breakdown"]}
        rows = []
        for finance in prime["allocations"]:
            vid = finance["venue"]
            revenue = revenues.get(vid)
            row = {
                "venue": vid,
                "label": finance["label"],
                "cost_status": finance["status"],
                "cost_of_funds": finance["cost_of_funds"],
                "gross_apy": None,
                "net_apy": None,
                "average_exposure": None,
                "denominator": None,
                "yield_status": None,
            }
            if finance["status"] in {"covered_by_boundary", "savings_liability", "income_only"}:
                row["yield_status"] = finance["status"]
            elif revenue is None:
                row["yield_status"] = "revenue_not_in_snapshot"
            else:
                average = D(revenue["tw_avg_value_usd"])
                row["denominator"] = "published time-weighted position value"
                if average <= 0:
                    average = D(revenue.get("tw_avg_notional_usd", "0"))
                    row["denominator"] = "published notional (position value unavailable)"
                if finance.get("average_facility_exposure") is not None:
                    # Facility claims track September deposits/redemptions;
                    # a fixed configured notional can predate those movements.
                    average = D(finance["average_facility_exposure"])
                    row["denominator"] = "daily outstanding facility principal; not escrow cash"
                elif prime["prime"] == "spark" and vid == "S23":
                    # The escrow balance is NOT the outstanding loan. Reuse
                    # the accepted full-principal return policy and daily loan
                    # amounts already established in the funding-origin work.
                    if anchorage is not None:
                        average = (
                            sum(
                                (
                                    D(
                                        anchorage["daily"][str(start + timedelta(days=i))][
                                            "facility_principal"
                                        ]
                                    )
                                    for i in range(n)
                                ),
                                D(0),
                            )
                            / n
                        )
                    else:
                        average = None
                    row["denominator"] = (
                        "daily outstanding Anchorage loan principal; not escrow cash"
                    )
                row["average_exposure"] = str(average) if average is not None else None
                gross_revenue = D(revenue["actual_revenue"]) + D(revenue["external_revenue"])
                row["gross_revenue"] = str(gross_revenue)
                row["prime_retained_revenue"] = revenue["revenue"]
                row["spread_reimbursement_not_in_venue_net"] = revenue.get(
                    "susds_spread_reimbursement", "0"
                )
                row["gross_apy"] = (
                    annualized_yield(gross_revenue, average, n) if average is not None else None
                )
                if finance["cost_of_funds"] is not None:
                    row["net_pnl"] = str(D(revenue["revenue"]) - D(finance["cost_of_funds"]))
                    row["net_apy"] = (
                        annualized_yield(D(row["net_pnl"]), average, n)
                        if average is not None
                        else None
                    )
                row["yield_status"] = (
                    "loan_exposure_unavailable"
                    if average is None
                    else "no_positive_exposure"
                    if average <= 0
                    else "available"
                    if row["net_apy"] is not None
                    else "gross_only"
                )
                for key in ["gross_apy", "net_apy"]:
                    row[key] = str(row[key]) if row[key] is not None else None
            rows.append(row)
        # Zero is a valid yield, not a missing result. Unknown yields sort last.
        rows.sort(
            key=lambda r: (
                r["gross_apy"] is not None,
                D(r["gross_apy"]) if r["gross_apy"] is not None else D(0),
            ),
            reverse=True,
        )
        output.append(
            {"prime": prime["prime"], "start": str(start), "end": str(end), "allocations": rows}
        )
    return {
        "sort": "gross_apy descending; unavailable last",
        "method": "(1 + period revenue / average exposure) ** (365 / days) - 1",
        "net_definition": "Prime-retained venue revenue minus modeled allocation borrowing costs; excludes Prime-level adjustments/spread reimbursements.",
        "results": output,
    }


def percent(value):
    if value is None:
        return "—"
    percentage = D(value) * 100
    if 0 < abs(percentage) < D("0.005"):
        if abs(percentage) < D("0.0001"):
            return ("-" if percentage < 0 else "") + "<0.0001%"
        return f"{percentage:.4f}%"
    return f"{percentage:.2f}%"


def markdown(result):
    month_label = date.fromisoformat(result["results"][0]["start"]).strftime("%B %Y")
    lines = [
        f"# {month_label} allocation yields",
        "",
        f"Annualized {month_label} returns (APY), sorted descending by gross APY within each Prime.",
        "Gross uses booked investment revenue. Net uses Prime-retained venue revenue after SDE, less modeled borrowing costs.",
        "These are not forward loan/reward rates. Cash-recognized distributions can be lumpy.",
        "Prime-level adjustments and spread reimbursements are not allocated into net APY; no published report changes.",
        "Anchorage uses average outstanding loan principal, not its pass-through escrow balance.",
        "",
        "## Positions averaging at least $1,000",
        "",
        "| Prime | Venue | Allocation | Average exposure | Gross APY | Net APY |",
        "|---|---|---|---:|---:|---:|",
    ]
    for prime in result["results"]:
        for r in prime["allocations"]:
            if r["gross_apy"] is not None and D(r["average_exposure"]) >= 1000:
                lines.append(
                    f"| {prime['prime']} | {r['venue']} | {r['label']} | ${D(r['average_exposure']):,.2f} | {percent(r['gross_apy'])} | {percent(r['net_apy'])} |"
                )
    lines += [
        "",
        "## Complete inventory (including dust and unavailable yields)",
        "",
        "| Prime | Venue | Allocation | Gross APY | Net APY | Status |",
        "|---|---|---|---:|---:|---|",
    ]
    for prime in result["results"]:
        for r in prime["allocations"]:
            lines.append(
                f"| {prime['prime']} | {r['venue']} | {r['label']} | {percent(r['gross_apy'])} | {percent(r['net_apy'])} | {r['yield_status']}; {r['cost_status']} |"
            )
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--month", default="2026-08")
    p.add_argument("--settlements-root", type=Path, default=Path("settlements"))
    p.add_argument("--anchorage-exposure", type=Path)
    p.add_argument("--readiness", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--markdown", type=Path, required=True)
    args = p.parse_args()
    paths = [
        args.readiness,
        args.settlements_root / "spark" / args.month / "provenance.json",
        args.settlements_root / "grove" / args.month / "provenance.json",
    ]
    anchorage_path = args.anchorage_exposure or (
        Path("reconciliation/spark_anchorage_receipt_funding_origins_2026_08.json.gz")
        if args.month == "2026-08"
        else None
    )
    if anchorage_path:
        paths.append(anchorage_path)
    if args.output.resolve() == args.markdown.resolve() or any(
        out.resolve() == source.resolve()
        for out in [args.output, args.markdown]
        for source in paths
    ):
        p.error("Output must not overwrite an input or another output")
    readiness = json.load(gzip.open(args.readiness, "rt"))
    for path in paths[1:3]:
        if readiness["input_sha256"][str(path)] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("Cost and revenue provenance differ")
    result = build(
        readiness,
        {
            prime: json.loads(path.read_text())
            for prime, path in zip(["spark", "grove"], paths[1:3], strict=True)
        },
        json.load(gzip.open(anchorage_path, "rt")) if anchorage_path else None,
    )
    result["input_sha256"] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }
    result["source_sha256"] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [Path(__file__), Path("src/settle/compute/allocation_financing.py")]
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    args.markdown.write_text(markdown(result))


if __name__ == "__main__":
    main()
