"""Decompose PSM3 value and retained saver-interest funding after offline replay.

The unexplained-value remainder is not classified as earnings. No funding or
settlement changes are made by this diagnostic.
"""

import argparse
import json
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_saved_funding_gap import read


def analyze(custody, psm, control):
    days = {r["date"]: r for r in control["sky_revenue_daily"]}
    expected = set(days)
    if not expected <= set(custody):
        raise ValueError("Missing custody funding days")
    result = {"publishable": False, "psm3": {}, "saver_interest": {}}
    with localcontext() as ctx:
        ctx.prec = 60
        rates = {d: D(r["daily_sky_rev"]) / D(r["utilized"]) for d, r in days.items()}
        accounts = {k for d in days for k in custody[d]}
        for account in sorted(accounts):
            is_psm = account.startswith("psm:")
            if not is_psm and not account.startswith("financing:saver-interest:"):
                raise ValueError("Unexpected custody account")
            chain = account.split(":")[1] if is_psm else None
            if is_psm:
                values = {r["block_date"]: D(r["cum_balance"]) for r in psm[chain]["daily"]}
                if set(values) != expected:
                    raise ValueError("PSM daily value coverage mismatch")
                check = psm[chain]["share_control"]
                if check["closing_shares_rpc"] != check["closing_shares_events"]:
                    raise ValueError("PSM closing shares mismatch")
            totals = {k: D(0) for k in ("sky", "savings", "full_value", "unattributed_value")}
            by_source = {}
            uncertain_days = []
            for d in days:
                r = custody[d][account]
                sky = D(r["sky_principal"])
                savings = sum(map(D, r["savings_by_source"].values()), D(0))
                value = values[d] if is_psm else D(r["observed_value"])
                for k, v in {
                    "sky": sky,
                    "savings": savings,
                    "full_value": value,
                    "unattributed_value": value - sky - savings,
                }.items():
                    totals[k] += v * rates[d]
                for source, amount in r["savings_by_source"].items():
                    by_source[source] = by_source.get(source, D(0)) + D(amount) * rates[d]
                if r["uncertain"]:
                    uncertain_days.append(d)
            # For financing accounts zero market value is intentional: the
            # cash has been spent. A negative remainder is not an asset loss.
            result["psm3" if is_psm else "saver_interest"][account] = {
                "rate_equivalents": totals,
                "savings_sources": by_source,
                "uncertain_days": uncertain_days,
                "interpretation": (
                    "Full market value less lender principal; remainder is not proven earnings."
                    if is_psm
                    else "Retained principal of spent interest payments under proportional funding policy; not an investment asset."
                ),
            }
        result["psm_totals"] = {
            k: sum((r["rate_equivalents"][k] for r in result["psm3"].values()), D(0))
            for k in totals
        }
        result["saver_interest_sky_cost"] = sum(
            (r["rate_equivalents"]["sky"] for r in result["saver_interest"].values()), D(0)
        )
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("replay-dir", "psm-dir", "control", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    status = read(a.replay_dir / "status.json")
    if status["stage"] != "completed_not_published":
        raise ValueError("Corrected replay must finish before analyzing custody")
    psm = {c: read(a.psm_dir / (c + ".json")) for c in ("arbitrum", "base", "optimism", "unichain")}
    result = analyze(read(a.replay_dir / "custody-funding.json.gz"), psm, read(a.control))
    a.output.write_text(json.dumps(result, default=str, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("psm3", "saver_interest")}, default=str
        )
    )


if __name__ == "__main__":
    main()
