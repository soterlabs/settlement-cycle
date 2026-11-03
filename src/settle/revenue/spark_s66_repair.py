"""Scoped August analytics repair from finalized S66 settlement evidence.

No settlement writes and no promotion of conditional funding to canonical cost.
"""

import copy
import gzip
import hashlib
import json
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

from openpyxl import load_workbook

from ..compute.allocation_financing import annualized_yield
from .allocations import indexed, month_bounds, period_return
from .conditional_allocations import decimal, same

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = Path("reconciliation/spark_s66_august_repair_manifest.json")


def evidence_paths(root=ROOT):
    manifest_path = root / MANIFEST
    manifest = json.loads(manifest_path.read_text())
    if (manifest["prime"], manifest["month"], manifest["venue"]) != ("spark", "2026-08", "S66"):
        raise ValueError("Unexpected S66 repair scope")
    paths = {}
    hashes = {"s66_repair_manifest": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
    for name, entry in manifest["evidence"].items():
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Evidence outside repository")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            raise ValueError("S66 repair evidence changed")
        paths[name] = path
        hashes["s66_repair_" + name] = actual
    return paths, hashes


def calculate(root=ROOT):
    paths, hashes = evidence_paths(root)

    def read(name):
        path = paths[name]
        raw = path.read_bytes()
        return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)

    funding = read("funding")
    rates = read("rates")
    idle = read("idle")
    if (
        funding["period"] != "2026-08"
        or rates["prime_id"] != "spark"
        or rates["period"]["start"] != "2026-08-01"
        or rates["period"]["end"] != "2026-08-31"
        or (idle["prime"], idle["month"], idle["venue"]) != ("spark", "2026-08", "S66")
    ):
        raise ValueError("S66 input period differs")
    candidate = indexed(funding["allocations"], "venue")["S66"]
    daily_rates = indexed(rates["sky_revenue_daily"], "date")
    start, end = month_bounds("2026-08")
    days = (end - start).days + 1
    dates = {str(start + timedelta(days=i)) for i in range(days)}
    if any(set(data) != dates for data in (candidate["daily"], daily_rates, idle["daily"])):
        raise ValueError("S66 requires complete daily principal, rates and deductions")
    cost = D(0)
    daily = {}
    for day in sorted(dates):
        capital = candidate["daily"][day]
        sky = decimal(capital["sky_principal"], nonnegative=True)
        savings = decimal(capital["savings_principal"], nonnegative=True)
        usds = decimal(idle["daily"][day], nonnegative=True)
        rate_row = daily_rates[day]
        utilized = decimal(rate_row["utilized"], nonnegative=True)
        if utilized <= 0:
            raise ValueError("Missing positive utilized rate control")
        rate = decimal(rate_row["daily_sky_rev"], nonnegative=True) / utilized
        # Preserve the accepted daily deduction convention and existing funding
        # estimate. Savings is already outside sky; do not deduct it twice.
        chargeable = max(D(0), sky - usds)
        charge = chargeable * rate
        cost += charge
        daily[day] = dict(
            sky_principal_usd=str(sky),
            savings_principal_usd=str(savings),
            idle_usds=str(usds),
            chargeable_sky_principal_usd=str(chargeable),
            daily_rate=str(rate),
            borrowing_cost_usd=str(charge),
        )
    same(cost, candidate["conditional_sky_borrowing_cost"])
    workbook = load_workbook(paths["workbook"], read_only=True, data_only=True)
    try:
        values = iter(workbook["Venues"].values)
        headers = next(values)
        rows = [dict(zip(headers, row, strict=True)) for row in values if row[0] == "S66"]
    finally:
        workbook.close()
    if len(rows) != 1:
        raise ValueError("Expected one finalized S66 workbook row")
    row = rows[0]
    # Float cells retain Excel's published precision; never reinterpret the
    # workbook's CoF allocation as the new funding-based borrowing charge.
    same(row["Weight"], D(1))
    same(row["sd_revenue (to Sky)"], D(0))
    if row["Notes"] != "(avg excl. idle AMM USDS)":
        raise ValueError("Workbook denominator deduction changed")
    actual = decimal(row["actual_revenue"])
    external = decimal(row["external_revenue"])
    retained = decimal(row["revenue (to prime)"])
    same(actual + external, retained)
    summary_rows = [
        line for line in paths["summary"].read_text().splitlines() if line.startswith("| S66 |")
    ]
    if len(summary_rows) != 1:
        raise ValueError("Expected one S66 summary row")
    cells = [cell.strip() for cell in summary_rows[0].split("|")[1:-1]]
    summary_actual = D(cells[5].replace("$", "").replace(",", ""))
    same(summary_actual, actual, tolerance=D(".005"))
    if cells[8] != "0%":
        raise ValueError("S66 is not SDE in the finalized report")
    idle_average = sum((decimal(x, nonnegative=True) for x in idle["daily"].values()), D(0)) / days
    exposure = decimal(row["Avg value"], nonnegative=True) + idle_average
    if exposure <= 0:
        raise ValueError("S66 requires positive full-position exposure")
    gross = actual + external
    net = retained - cost
    return dict(
        prime="spark",
        month="2026-08",
        venue="S66",
        gross_revenue_usd=str(gross),
        prime_retained_revenue_usd=str(retained),
        average_yield_exposure_usd=str(exposure),
        average_idle_usds=str(idle_average),
        workbook_cost_allocation_usd=str(decimal(row["CoF alloc"])),
        borrowing_cost_usd=str(cost),
        funding_status="conditional_estimate",
        funding_validated=False,
        average_sky_principal_usd=str(
            sum(decimal(r["sky_principal_usd"]) for r in daily.values()) / days
        ),
        average_savings_principal_usd=str(
            sum(decimal(r["savings_principal_usd"]) for r in daily.values()) / days
        ),
        uncertain_days=candidate["uncertain_days"],
        net_pnl_usd=str(net),
        gross_monthly_return=period_return(str(gross), str(exposure)),
        net_monthly_return=period_return(str(net), str(exposure)),
        gross_apy=str(annualized_yield(gross, exposure, days)),
        net_apy=str(annualized_yield(net, exposure, days)),
        yield_denominator="finalized workbook average plus daily idle-USDS deduction (full position)",
        daily=daily,
        source_hashes=hashes,
    )


def repair_document(base, root=ROOT):
    if (base["prime"], base["month"]) != ("spark", "2026-08") or base.get("venue") is not None:
        raise ValueError("Repair requires complete Spark August document")
    rows = indexed(base["allocations"], "venue_id")
    if len(rows) != base["count"] or base.get("provisional") is not True:
        raise ValueError("Invalid baseline coverage or status")
    calculated = calculate(root)
    row = rows["S66"]
    estimate = row["conditional_funding_estimate"]
    if estimate["funding_validated"] is not False or estimate["status"] != "conditional_estimate":
        raise ValueError("Expected conditional S66 funding")
    for key in ("borrowing_cost_usd", "average_sky_principal_usd", "average_savings_principal_usd"):
        same(estimate[key], calculated[key])
    if sorted(estimate["uncertain_days"]) != sorted(calculated["uncertain_days"]):
        raise ValueError("Baseline S66 funding uncertainty differs")
    if any(row[key] is not None for key in ("borrowing_cost_usd", "net_pnl_usd", "net_apy")):
        raise ValueError("Do not overwrite canonical S66 funding")
    fields = (
        "gross_revenue_usd",
        "prime_retained_revenue_usd",
        "average_yield_exposure_usd",
        "gross_monthly_return",
        "gross_apy",
    )
    for key in fields:
        if row[key] is not None:
            same(row[key], calculated[key])
    result = copy.deepcopy(base)
    previous = result.pop("revision_id")
    result.pop("published_at", None)
    target = next(r for r in result["allocations"] if r["venue_id"] == "S66")
    target.update({key: calculated[key] for key in fields})
    target["yield_denominator"] = calculated["yield_denominator"]
    target["yield_status"] = "gross_only"
    target["conditional_funding_estimate"].update(
        {key: calculated[key] for key in ("net_pnl_usd", "net_monthly_return", "net_apy")}
    )
    target["conditional_funding_estimate"]["yield_status"] = "conditional_estimate"
    result["source_hashes"].update(calculated["source_hashes"])
    result.setdefault(
        "s66_august_repair",
        dict(
            base_revision_id=previous,
            scope="S66 revenue and yield only; conditional borrowing cost unchanged",
            denominator=calculated["yield_denominator"],
        ),
    )
    return result
