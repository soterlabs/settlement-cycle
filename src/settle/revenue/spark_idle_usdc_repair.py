"""Repair August idle-USDC yield exposure without certifying funding splits."""

import copy
import gzip
import hashlib
import json
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

from ..compute.allocation_financing import annualized_yield
from .allocations import indexed, month_bounds, period_return
from .conditional_allocations import decimal, same

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path("reconciliation/spark_idle_usdc_august_exposure.json")
EXPOSURE_SHA256 = "f3f7dba3d5b4a2af6208d3dbe42c628af40bcefbbbcc3689c3a16a412483c332"
VENUES = {"S45", "S53", "S55"}


def repair_document(base, root=ROOT):
    if (base["prime"], base["month"]) != ("spark", "2026-08"):
        raise ValueError("Idle-USDC repair is limited to Spark August")
    if base.get("venue") is not None or base.get("provisional") is not True:
        raise ValueError("Complete provisional baseline required")
    rows = indexed(base["allocations"], "venue_id")
    if len(rows) != base["count"]:
        raise ValueError("Invalid allocation coverage")
    raw = (root / EVIDENCE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPOSURE_SHA256:
        raise ValueError("Idle-USDC exposure evidence changed")
    evidence = json.loads(raw)
    if (evidence["prime"], evidence["month"]) != ("spark", "2026-08"):
        raise ValueError("Exposure period differs")
    if set(evidence["allocations"]) != VENUES:
        raise ValueError("Exposure scope differs")
    # Pin the existing cost evidence separately. This repair changes no costs.
    manifest = json.loads(
        (root / "reconciliation/spark_s66_august_repair_manifest.json").read_text()
    )
    entry = manifest["evidence"]["funding"]
    funding_raw = (root / entry["path"]).read_bytes()
    if hashlib.sha256(funding_raw).hexdigest() != entry["sha256"]:
        raise ValueError("Conditional funding evidence changed")
    funding = json.loads(gzip.decompress(funding_raw))
    if funding["period"] != "2026-08":
        raise ValueError("Funding period differs")
    candidates = indexed(funding["allocations"], "venue")
    start, end = month_bounds("2026-08")
    days = (end - start).days + 1
    dates = {str(start + timedelta(days=i)) for i in range(days)}
    result = copy.deepcopy(base)
    previous = result.pop("revision_id")
    result.pop("published_at", None)
    targets = indexed(result["allocations"], "venue_id")
    for venue in sorted(VENUES):
        row = rows[venue]
        estimate = row["conditional_funding_estimate"]
        if (
            estimate["funding_validated"] is not False
            or estimate["status"] != "conditional_estimate"
        ):
            raise ValueError("Expected conditional funding")
        if any(
            row[k] is not None
            for k in ("borrowing_cost_usd", "net_pnl_usd", "net_apy", "net_monthly_return")
        ):
            raise ValueError("Cannot overwrite canonical financing")
        same(row["gross_revenue_usd"], D(0))
        same(row["prime_retained_revenue_usd"], D(0))
        cost = decimal(estimate["borrowing_cost_usd"], nonnegative=True)
        same(cost, candidates[venue]["conditional_sky_borrowing_cost"])
        same(estimate["net_pnl_usd"], -cost)
        daily = evidence["allocations"][venue]["daily"]
        if set(daily) != dates:
            raise ValueError("Complete daily USDC exposure required")
        exposure = sum((decimal(v, nonnegative=True) for v in daily.values()), D(0)) / days
        if exposure <= 0:
            raise ValueError("Positive USDC exposure required")
        target = targets[venue]
        target.update(
            average_yield_exposure_usd=str(exposure),
            yield_denominator="daily closing USDC at par (full position)",
            gross_monthly_return=period_return("0", str(exposure)),
            gross_apy=str(annualized_yield(D(0), exposure, days)),
            yield_status="gross_only",
        )
        target["conditional_funding_estimate"].update(
            net_monthly_return=period_return(str(-cost), str(exposure)),
            net_apy=str(annualized_yield(-cost, exposure, days)),
            yield_status="conditional_estimate",
        )
    result["source_hashes"]["idle_usdc_august_exposure"] = EXPOSURE_SHA256
    result.setdefault(
        "idle_usdc_august_repair",
        {
            "base_revision_id": previous,
            "scope": "S45/S53/S55 yield exposure only; conditional costs unchanged",
        },
    )
    return result
