"""Reviewed conditional estimates, separate from canonical allocation values."""

from __future__ import annotations

import copy
from datetime import timedelta
from decimal import Decimal as D

from ..compute.allocation_financing import annualized_yield
from .allocations import indexed, month_bounds, number, period_return

ZERO = D(0)


def decimal(value, *, nonnegative=False):
    parsed = number(value, nonnegative=nonnegative)
    if parsed is None:
        raise ValueError("Required conditional amount is unavailable")
    return D(parsed)


def same(actual, expected, *, tolerance=D("1e-8")):
    if actual is None or expected is None:
        if actual is not None or expected is not None:
            raise ValueError("Conditional estimate availability differs from reviewed inputs")
    elif abs(decimal(actual) - decimal(expected)) > tolerance:
        raise ValueError("Conditional estimate differs from reviewed inputs")


def build_conditional_document(
    base, evidence, bridge, savings, control, anchorage, *, source_hashes
):
    """Enrich a complete baseline without replacing any existing row values.

    Caller verifies evidence file hashes against the reviewed manifest. This
    narrower contract deliberately does not promote research costs into the
    canonical borrowing_cost_usd/net_apy fields.
    """
    if (base["prime"], base["month"]) != ("spark", "2026-08"):
        raise ValueError("Reviewed publication is limited to Spark August 2026")
    if base.get("provisional") is not True or base.get("venue") is not None:
        raise ValueError("A complete provisional baseline is required")
    if (
        evidence["period"] != base["month"]
        or bridge["period"] != base["month"]
        or savings["period"] != base["month"]
    ):
        raise ValueError("Conditional evidence periods differ")
    if any(r.get("publishable") is not False for r in (evidence, bridge, savings)):
        raise ValueError("Expected explicitly conditional research inputs")
    start, end = month_bounds(base["month"])
    days = (end - start).days + 1
    expected_days = {str(start + timedelta(days=i)) for i in range(days)}
    if control["prime_id"] != base["prime"] or (
        control["period"]["start"],
        control["period"]["end"],
    ) != (str(start), str(end)):
        raise ValueError("Revenue control period differs")
    controls = indexed(control["venue_breakdown"], "venue_id")
    rows = indexed(base["allocations"], "venue_id")
    candidates = indexed(evidence["allocations"], "venue")
    if (
        len(rows) != base["count"]
        or len(candidates) != bridge["allocation_count"]
        or set(candidates) - rows.keys()
    ):
        raise ValueError("Conditional allocation coverage differs")
    if len(candidates) != evidence["configured_allocations"] or evidence["available_costs"] != len(
        candidates
    ):
        raise ValueError("Incomplete reviewed allocation costs")
    total = ZERO
    estimates = {}
    for venue, candidate in candidates.items():
        row = rows[venue]
        daily = candidate["daily"]
        if set(daily) != expected_days:
            raise ValueError("Conditional principal must cover every day")
        for values in daily.values():
            for field in ("sky_principal", "savings_principal", "original_capital_control"):
                decimal(values[field], nonnegative=True)
            if type(values["uncertain"]) is not bool:
                raise ValueError("Invalid funding uncertainty flag")
        for field in ("sky", "savings"):
            average = sum((decimal(d[f"{field}_principal"]) for d in daily.values()), ZERO) / days
            same(average, candidate[f"average_{field}_principal"])
        cost = decimal(candidate["conditional_sky_borrowing_cost"], nonnegative=True)
        total += cost
        same(row["gross_apy"], candidate["gross_apy"], tolerance=D("1e-25"))
        venue_control = controls.get(venue)
        expected_exposure = expected_gross = expected_retained = None
        if venue_control is not None:
            expected_exposure = decimal(venue_control["tw_avg_value_usd"], nonnegative=True)
            if expected_exposure == 0:
                expected_exposure = decimal(
                    venue_control.get("tw_avg_notional_usd", "0"), nonnegative=True
                )
            if venue == "S23":
                expected_exposure = (
                    sum(
                        (
                            decimal(anchorage["daily"][d]["facility_principal"], nonnegative=True)
                            for d in sorted(expected_days)
                        ),
                        ZERO,
                    )
                    / days
                )
            expected_gross = decimal(venue_control["actual_revenue"]) + decimal(
                venue_control["external_revenue"]
            )
            expected_retained = decimal(venue_control["revenue"])
        # Older API revisions used a less precise denominator label. Compare
        # the actual value against reviewed controls, not presentation text.
        same(row["average_yield_exposure_usd"], expected_exposure)
        same(row["gross_revenue_usd"], expected_gross)
        same(row["prime_retained_revenue_usd"], expected_retained)
        retained = row["prime_retained_revenue_usd"]
        net = None if retained is None else decimal(retained) - cost
        exposure = row["average_yield_exposure_usd"]
        apy = (
            None
            if net is None or exposure is None
            else annualized_yield(net, decimal(exposure, nonnegative=True), days)
        )
        same(apy, candidate["conditional_net_apy"], tolerance=D("1e-25"))
        uncertain = sorted(day for day, d in daily.items() if d["uncertain"])
        if uncertain != sorted(candidate["uncertain_days"]):
            raise ValueError("Funding uncertainty days differ")
        estimates[venue] = {
            "status": "conditional_estimate",
            "funding_validated": False,
            "average_sky_principal_usd": str(
                decimal(candidate["average_sky_principal"], nonnegative=True)
            ),
            "average_savings_principal_usd": str(
                decimal(candidate["average_savings_principal"], nonnegative=True)
            ),
            "borrowing_cost_usd": str(cost),
            "net_pnl_usd": None if net is None else str(net),
            "net_monthly_return": period_return(None if net is None else str(net), exposure),
            "net_apy": None if apy is None else str(apy),
            "yield_status": "unavailable" if apy is None else "conditional_estimate",
            "uncertain_days": uncertain,
        }
    same(total, bridge["allocation_cost"])
    same(total, evidence["allocation_cost"])
    same(
        bridge["global_cost"],
        decimal(bridge["global_excluding_msc"]) + decimal(bridge["msc_financing_cost"]),
    )
    components = bridge["components"]
    adjustments = {
        "other_captured_account_financing_usd": decimal(components["outside_snapshot_selected"]),
        "unclassified_sky_financing_cost_usd": decimal(components["debt_without_captured_basis"]),
        "vat_rate_accrual_financing_usd": decimal(components["vat"]),
        "deduction_difference_financing_usd": decimal(components["deduction_difference"]),
    }
    residual = decimal(bridge["global_excluding_msc"]) - total - sum(adjustments.values(), ZERO)
    same(residual, ZERO)
    same(residual, bridge["arithmetic_residual"])
    same(
        decimal(bridge["global_cost"]),
        sum((decimal(d["daily_sky_rev"]) for d in control["sky_revenue_daily"]), ZERO),
    )
    unassigned_savings = sum(
        (
            decimal(s["outside_captured_accounts_or_financing_adjustments"], nonnegative=True)
            for s in savings["source_bridge"]
        ),
        ZERO,
    )
    result = copy.deepcopy(base)
    base_revision = result.pop("revision_id")
    result.pop("published_at", None)
    result["schema_version"] = "1.1"
    result["source_hashes"] = {**result["source_hashes"], **source_hashes}
    result["conditional_funding"] = {
        "status": "conditional_estimates_only",
        "scope": "whole_prime_month",
        "financing_bridge": {
            **{k: str(v) for k, v in adjustments.items()},
            "arithmetic_residual_usd": str(residual),
        },
        "base_revision_id": base_revision,
        "funding_reconciliation_status": "unresolved",
        "allocation_count": len(estimates),
        "allocation_cost_total_usd": str(total),
        "global_cost_excluding_msc_usd": number(bridge["global_excluding_msc"]),
        "msc_financing_cost_usd": number(bridge["msc_financing_cost"]),
        "unclassified_sky_financing_cost_usd": number(
            bridge["components"]["debt_without_captured_basis"]
        ),
        "savings_principal_outside_captured_accounts_usd": str(unassigned_savings),
        "policy": "Proportional Savings repayment/refinancing model; not certified funding provenance.",
        "net_definition": "Prime-retained revenue minus conditional Sky borrowing cost; excludes Savings VSR and prime-level financing adjustments.",
        "limitation": "Do not mix canonical costs and these estimates when summing allocations. Unclassified financing is not assigned to venues.",
    }
    for row in result["allocations"]:
        if row["venue_id"] in estimates:
            row["conditional_funding_estimate"] = estimates[row["venue_id"]]
    return result
