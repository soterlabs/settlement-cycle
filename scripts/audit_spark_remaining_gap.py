"""Refine PSM, raw-USDS and capped-venue deduction attribution from saved evidence."""

import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_saved_funding_gap import check, decompose, read


def main():
    parser = argparse.ArgumentParser(
        description="Refine the saved Spark funding-cost residual without replay or publication."
    )
    parser.add_argument("--saved-dir", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--idle", type=Path, required=True)
    parser.add_argument("--routes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, control = args.saved_dir, args.control
    inputs = [
        control,
        *[
            root / n
            for n in [
                "funding-snapshot.json.gz",
                "conditional-costs.json.gz",
                "daily-by-ilk.json.gz",
                "vat-accrual.json",
            ]
        ],
        args.idle,
        args.routes,
    ]
    p, s, c, accounts, vat, idle, routes = [read(p) for p in inputs]
    r = decompose(p, s, c, accounts, vat)
    with localcontext() as ctx:
        ctx.prec = 60
        rates = {
            x["date"]: D(x["daily_sky_rev"]) / D(x["utilized"]) for x in p["sky_revenue_daily"]
        }
        sd = {
            v["venue_id"]: {x["block_date"]: D(x["cum_value"]) for x in v["daily"]}
            for v in p["sde_daily_breakdown"]
        }
        psm_usdc = {
            x["date"]: D(x["sde_av"]) - sum(v.get(x["date"], D(0)) for v in sd.values())
            for x in p["sky_revenue_daily"]
        }
        psm_sde_cost = sum(psm_usdc[d] * rates[d] for d in rates)
        floors = {}
        daily_floors = {}
        for v in c["allocations"]:
            if v["conditional_sky_borrowing_cost"] is None:
                continue
            vals = {
                d: max(
                    D(0),
                    D(idle["daily"].get(v["venue"], {}).get(d, 0))
                    + sd.get(v["venue"], {}).get(d, D(0))
                    - D(v["daily"][d]["sky_principal"]),
                )
                * rates[d]
                for d in rates
            }
            if sum(vals.values()) > D(".000001"):
                floors[v["venue"]] = sum(vals.values())
                daily_floors[v["venue"]] = vals
        raw = []
        with gzip.open(root / "funded-history.jsonl.gz", "rt") as f:
            h = json.loads(next(f))
        for a in h["idle_accounts"]:
            sums = {k: D(0) for k in ["sky", "savings", "other_value", "observed"]}
            for d, rate in rates.items():
                x = s["daily_funding"][d][a]
                sky = D(x["sky_principal"])
                sv = sum((D(v) for v in x["savings_by_source"].values()), D(0))
                observed = D(x["observed_value"])
                for k, n in [
                    ("sky", sky),
                    ("savings", sv),
                    ("other_value", observed - sky - sv),
                    ("observed", observed),
                ]:
                    sums[k] += n * rate
            raw.append({"account": a, **sums})
        check(
            sum(a["observed"] for a in raw),
            r["global_deductions"]["alm_usds"],
            "ALM observed control",
        )
        applied_raw = sum(a["sky"] for a in raw)
        psm_gross = r["account_groups"]["psm"]
        psm_net = psm_gross - psm_sde_cost - r["global_deductions"]["psm_usds"]
        deduction_gap = applied_raw - r["global_deductions"]["alm_usds"]
        tagged = D(routes["associated_residual_cost"])
        remaining = {
            "PSM_gross_less_global_USDS_and_USDC_deductions": psm_net,
            "savings_interest_financing_accounts": r["account_groups"]["financing"],
            "unmatched_outflow_accounts_after_tagging_RLUSD_deposits": r["account_groups"][
                "unallocated"
            ]
            - tagged,
            "cctp_transit": r["account_groups"]["cctp"],
            "unpriced_selected": r["components"]["unpriced_selected"],
            "rounding": r["account_groups"]["rounding"],
            "raw_USDS_deduction_beyond_Sky_basis": deduction_gap,
            **{"deduction_floor_" + v: -cost for v, cost in floors.items()},
            "independent_cash_debt_difference": r["components"]["cash_debt_gap"],
        }
        target = r["remaining_cost"] - tagged
        check(sum(remaining.values()), target, "refined residual")
        result = {
            "publishable": False,
            "scope": "Refined existing residual after tagging deposits; no corrected replay or reassignment.",
            "remaining_components": remaining,
            "remaining_total": target,
            "psm_gross": psm_gross,
            "psm_usds_deduction": r["global_deductions"]["psm_usds"],
            "psm_usdc_sde_deduction": psm_sde_cost,
            "raw_USDS_funding_cost_equivalents": raw,
            "daily_deduction_floors": daily_floors,
            "inputs": {str(x): hashlib.sha256(x.read_bytes()).hexdigest() for x in inputs},
        }
        args.output.write_text(json.dumps(result, default=str, indent=2) + "\n")
        for k, v in remaining.items():
            print(k, round(v, 4))
        print("TOTAL", target)
        print(
            "raw Savings",
            sum(x["savings"] for x in raw),
            "raw other",
            sum(x["other_value"] for x in raw),
        )
        for v, ds in daily_floors.items():
            print("floor days", v, len([x for x in ds.values() if x > D(".01")]))


if __name__ == "__main__":
    main()
