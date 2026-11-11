"""Refresh three September allocation revenue rows; never settlement records."""
import argparse
import copy
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

from prepare_spark_monthly_cost_publication import publish_reviewed

from settle.compute.allocation_financing import annualized_yield
from settle.revenue.allocations import indexed, period_return
from settle.revenue.verification import digest
from settle.store.db import connect

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "reconciliation/spark_september_allocation_revenue"
TARGETS = {"S64", "S70", "S72"}


def refresh(baseline, evidence):
    doc = copy.deepcopy(baseline)
    revision = doc.pop("revision_id")
    doc.pop("published_at")
    if revision != evidence["baseline_revision"] or digest(doc) != revision:
        raise ValueError("Baseline revision/content differs from reviewed publication")
    if (doc["prime"], doc["month"], doc["period"]) != (
        "spark", "2026-09", {"start": "2026-09-01", "end": "2026-09-30", "days": 30}
    ):
        raise ValueError("Only Spark September is authorized")
    rows = indexed(doc["allocations"], "venue_id")
    if not TARGETS <= rows.keys() or set(evidence["venues"]) != {"S70", "S72"}:
        raise ValueError("Unexpected venue coverage")
    cash, seen = D(0), set()
    for r in evidence["receipts"]:
        key = (r["transaction_hash"], r["log_index"])
        if (key in seen or not 1788220800 <= r["block_time"] < 1790812800
                or not evidence["pins"][0] < r["block_number"] <= evidence["pins"][1]
                or r["address"] != "0x8292bb45bf1ee4d140127049757c2e0ff06317ed"
                or r["topic0"] != "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
                or r["topic1"] != "0x" + "b70e0fad7d0cf4c187365a84e728802ab24ca78b".zfill(64)
                or r["topic2"] != "0x" + "1601843c5e9bc251a3272907010afa41fa18347e".zfill(64)):
            raise ValueError("Invalid or duplicate issuer receipt")
        seen.add(key)
        amount = D(int(r["data"], 16)) / 10**18
        if amount <= 0:
            raise ValueError("Invalid receipt amount")
        cash += amount
    if cash != D("1373208") or len(seen) != 6:
        raise ValueError("Reviewed September receipt total differs")
    for venue in sorted(TARGETS):
        row = rows[venue]
        if venue == "S64":
            # Replace the stale zero with cash booked by PR267; S67 accrual is
            # separate reporting information, never another income addition.
            gross = retained = cash
            average = D(row["average_yield_exposure_usd"])
        else:
            source = evidence["venues"][venue]
            if source["venue_id"] != venue or D(source["sd_share"]) != 0:
                raise ValueError("Unexpected source venue/share")
            gross = D(source["actual_revenue"]) + D(source["external_revenue"])
            retained = D(source["revenue"])
            if gross != retained:
                raise ValueError("Non-SDE retained revenue differs")
            average = D(source["tw_avg_value"])
            row["average_yield_exposure_usd"] = str(average)
            row["yield_denominator"] = "retained replay time-weighted position value"
            row["spread_reimbursement_excluded_from_net_usd"] = source["susds_spread_reimbursement"]
        cost = D(row["borrowing_cost_usd"])
        if not all(x.is_finite() for x in (gross, retained, average, cost)) or average <= 0 or cost < 0:
            raise ValueError("Invalid revenue/exposure/cost")
        net = retained - cost
        row.update(gross_revenue_usd=str(gross), prime_retained_revenue_usd=str(retained),
                   net_pnl_usd=str(net), gross_monthly_return=period_return(gross, average),
                   net_monthly_return=period_return(net, average),
                   gross_apy=str(annualized_yield(gross, average, 30)),
                   net_apy=str(annualized_yield(net, average, 30)), yield_status="available")
    doc["limitations"].append(
        "S64 books RLUSD issuer cash receipts across the eligible RLUSD portfolio; "
        "its cash-recognized APY is not a standalone contractual return on idle RLUSD. "
        "S67 accrual is not added to these receipts."
    )
    doc["source_hashes"]["september_revenue_refresh"] = digest(evidence)
    doc["revenue_refresh"] = {"baseline_revision": revision, "venues": sorted(TARGETS),
                              "source_pr": evidence["source_pr"], "source_commit": evidence["source_commit"],
                              "scope": "API analytics only; no additional settlement adjustment"}
    return doc


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--publish", action="store_true")
    a = p.parse_args()
    if a.output.resolve().is_relative_to(ROOT):
        p.error("Output must be outside the repository to preserve evidence")
    paths = [FOLDER / "baseline.json", FOLDER / "evidence.json"]
    baseline, evidence = [json.loads(path.read_text()) for path in paths]
    doc = refresh(baseline, evidence)
    doc["source_hashes"].update({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    a.output.write_text(json.dumps(doc, indent=2) + "\n")
    if a.publish:
        with connect() as conn, conn.transaction():
            revision = publish_reviewed(conn, doc, baseline["revision_id"])
        print(json.dumps({"published": revision, "venues": sorted(TARGETS)}))
    else:
        print(json.dumps({"publish": False, "venues": sorted(TARGETS)}))


if __name__ == "__main__":
    main()
