"""Export September working venue costs without distributing reconciliation gaps."""

import argparse
import csv
import hashlib
import json
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_saved_funding_gap import read

from settle.domain.config import load_prime_by_id


def build(costs, reconciliation, custody, venues):
    if (costs["start"], costs["end"]) != ("2026-09-01", "2026-09-30"):
        raise ValueError("This reviewed decision applies to September 2026 only")
    source_rows = {r["venue"]: r for r in costs["allocations"]}
    if len(source_rows) != len(costs["allocations"]):
        raise ValueError("Duplicate venue costs")
    rows = []
    with localcontext() as ctx:
        ctx.prec = 60
        for venue, label in venues.items():
            r = source_rows.get(venue)
            if r is None or r["conditional_sky_borrowing_cost"] is None:
                rows.append(
                    {
                        "venue": venue,
                        "label": label,
                        "borrowing_cost_usd": None,
                        "average_sky_funded_principal_usd": None,
                        "average_savings_funded_principal_usd": None,
                        "status": "not_calculated_in_this_replay",
                    }
                )
                continue
            if len(r["daily"]) != 30 or set(r["daily"]) != {
                f"2026-09-{d:02d}" for d in range(1, 31)
            }:
                raise ValueError("Incomplete September funding")
            cost = D(r["conditional_sky_borrowing_cost"])
            if not cost.is_finite() or cost < 0:
                raise ValueError("Invalid venue borrowing cost")
            rows.append(
                {
                    "venue": venue,
                    "label": label,
                    "borrowing_cost_usd": cost,
                    "average_sky_funded_principal_usd": sum(
                        (D(d["sky_principal"]) for d in r["daily"].values()), D(0)
                    )
                    / 30,
                    "average_savings_funded_principal_usd": sum(
                        (D(d["savings_principal"]) for d in r["daily"].values()), D(0)
                    )
                    / 30,
                    "status": "working_estimate_existing_funding_policy",
                    "funding_uncertain_days": sum(
                        bool(d["uncertain"]) for d in r["daily"].values()
                    ),
                }
            )
        total = sum(
            (r["borrowing_cost_usd"] for r in rows if r["borrowing_cost_usd"] is not None), D(0)
        )
        if abs(total - D(reconciliation["existing_conditional_allocation_cost"])) > D("0.000001"):
            raise ValueError("Venue export does not reproduce replay subtotal")
        credits = sum((D(r["cost_of_funds"]) for r in reconciliation["additional_cost_rows"]), D(0))
        if abs(total + credits - D(reconciliation["allocation_cost_including_excess_credits"])) > D(
            "0.000001"
        ):
            raise ValueError("Deduction-credit bridge does not reconcile")
        return {
            "prime": "spark",
            "month": "2026-09",
            "publication_status": "not_published",
            "decision": "Operator accepted deferral of the remaining PSM3 funding-value difference on 2026-11-11. It is not distributed across venues.",
            "acknowledged_psm3_unattributed_value_cost_equivalent_usd": custody["psm_totals"][
                "unattributed_value"
            ],
            "scope": "Working borrowing-cost estimates using existing replay funding conventions; not certification of all source attribution or settlement revenue.",
            "venue_cost_subtotal_usd": total,
            "separate_deduction_credits_usd": credits,
            "subtotal_after_separate_credits_usd": total + credits,
            "global_borrowing_cost_usd": reconciliation["global_cost"],
            "separate_financing_costs": reconciliation["separate_financing_costs"],
            "comparison_difference_after_financing_usd": reconciliation["unassigned_cost"],
            "allocations": rows,
            "limitations": [
                "No PSM allocation is created and no unresolved difference is redistributed.",
                "Savings-interest financing and smaller residuals remain separately identified; their treatment is not approved by the PSM deferral.",
                "Missing rows remain null. Savings liability and incentive rows are not assumed to be funded investment positions.",
                "Revenue and yields are not regenerated; RLUSD revenue coverage is handled separately in PR267.",
            ],
        }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--replay-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    paths = [
        a.replay_dir / n
        for n in ("conditional-costs.json.gz", "reconciliation.json", "custody-analysis.json")
    ]
    names = {v.id: v.label for v in load_prime_by_id("spark").venues}
    names["S70"] = "SparkLend RLUSD"
    names["S72"] = "Sentora RLUSD vault"
    result = build(*map(read, paths), names)
    result["input_sha256"] = {
        str(f): hashlib.sha256(f.read_bytes()).hexdigest()
        for f in [*paths, Path("config/spark.yaml")]
    }
    a.output.write_text(json.dumps(result, default=str, indent=2) + "\n")
    with a.output.with_suffix(".csv").open("w") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "venue",
                "label",
                "borrowing_cost_usd",
                "average_sky_funded_principal_usd",
                "average_savings_funded_principal_usd",
                "status",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(result["allocations"])
    print(
        json.dumps(
            {
                "priced": sum(r["borrowing_cost_usd"] is not None for r in result["allocations"]),
                "not_calculated": [
                    r["venue"] for r in result["allocations"] if r["borrowing_cost_usd"] is None
                ],
                "venue_subtotal": result["venue_cost_subtotal_usd"],
            },
            default=str,
        )
    )


if __name__ == "__main__":
    main()
