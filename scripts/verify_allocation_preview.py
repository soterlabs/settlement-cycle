#!/usr/bin/env python3
"""Independent arithmetic check of a monthly allocation publication document.

Recomputes each published field from the settlement control's venue revenue
and the document's own cost/exposure, without importing settle code, so a bug
in the yield builder cannot also hide in its check. The automated monthly job
publishes only when ``problems`` and ``control_venues_without_row`` are empty.

    python scripts/verify_allocation_preview.py <preview.json> <provenance.json>
"""
import json
import sys
from decimal import Decimal as D
from decimal import localcontext


def _apy(r, days):
    # (1 + r) ** (365 / days) - 1 via exp/ln in Decimal
    return ((D(1) + r).ln() * D(365) / D(days)).exp() - 1


def verify(doc, control):
    with localcontext() as ctx:
        ctx.prec = 60
        days = doc["period"]["days"]
        rev = {v["venue_id"]: v for v in control["venue_breakdown"]}
        problems, total_cost = [], D(0)
        for row in doc["allocations"]:
            v = row["venue_id"]
            c = rev.get(v)
            cost = None if row["borrowing_cost_usd"] is None else D(row["borrowing_cost_usd"])
            if cost is not None:
                total_cost += cost
            if c is not None and row["gross_revenue_usd"] is not None:
                gross = D(c["actual_revenue"]) + D(c["external_revenue"])
                if abs(gross - D(row["gross_revenue_usd"])) > D("1e-12"):
                    problems.append(f"{v}: gross {row['gross_revenue_usd']} != control {gross}")
                if abs(D(c["revenue"]) - D(row["prime_retained_revenue_usd"])) > D("1e-12"):
                    problems.append(f"{v}: retained != control revenue")
            if row["net_pnl_usd"] is not None:
                if cost is None:
                    problems.append(f"{v}: net P&L present without a borrowing cost")
                elif abs(D(row["net_pnl_usd"]) - (D(row["prime_retained_revenue_usd"]) - cost)) > D("1e-20"):
                    problems.append(f"{v}: net != retained - cost")
            avg = row["average_yield_exposure_usd"]
            for profit, mret, ap in (
                (row["gross_revenue_usd"], row["gross_monthly_return"], row["gross_apy"]),
                (row["net_pnl_usd"], row["net_monthly_return"], row["net_apy"]),
            ):
                if profit is None or avg is None or D(avg) <= 0:
                    if ap is not None:
                        problems.append(f"{v}: APY present without positive exposure/profit")
                    continue
                r = D(profit) / D(avg)
                if mret is None or abs(r - D(mret)) > D("1e-20"):
                    problems.append(f"{v}: monthly return {mret} != {r}")
                elif ap is None or abs(_apy(r, days) - D(ap)) > D("1e-18"):
                    problems.append(f"{v}: APY {ap} != {_apy(r, days)}")
        missing = sorted(set(rev) - {r["venue_id"] for r in doc["allocations"]})
        global_cost = D(control["results"]["sky_revenue"]) - D(control["results"].get("sde_revenue", 0))
        return {
            "prime": doc["prime"], "month": doc["month"], "rows": len(doc["allocations"]),
            "rows_with_cost": sum(r["borrowing_cost_usd"] is not None for r in doc["allocations"]),
            "rows_with_net_apy": sum(r["net_apy"] is not None for r in doc["allocations"]),
            "known_allocation_cost": str(round(total_cost, 2)),
            "global_borrowing_cost_ex_sde": str(round(global_cost, 2)),
            "unallocated_difference": str(round(global_cost - total_cost, 2)),
            "control_venues_without_row": missing,
            "statuses": sorted({f"{r['yield_status']}/{r['borrowing_cost_status']}" for r in doc["allocations"]}),
            "problems": problems,
        }


def passes(report):
    return not report["problems"] and not report["control_venues_without_row"]


def main():
    preview, control = (json.load(open(p)) for p in sys.argv[1:3])
    if len(preview) != 1:
        raise SystemExit("expected exactly one document in the preview")
    report = verify(preview[0], control)
    print(json.dumps(report, indent=1))
    raise SystemExit(0 if passes(report) else 1)


if __name__ == "__main__":
    main()
