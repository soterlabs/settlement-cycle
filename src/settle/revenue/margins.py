"""Spark reporting margins; immutable inputs, no charge allocation or replay.

The portfolio statement perimeter is broader than the legacy allocation capital
publication. Never label a covered-venue subtotal as the full Spark denominator.
"""

import base64
import calendar
import json
from copy import deepcopy
from decimal import Decimal, localcontext

from .financial import metric
from .reporting_expenses import monthly as expenses_month
from .statement_rollups import combine
from .verification import digest

POLICY = "spark-reporting-margins.v1"


def encode(value):
    return (
        base64.urlsafe_b64encode(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def decode(value, prime, year):
    try:
        d = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if (
            set(d) != {"policy", "prime", "year", "statement", "allocations", "expenses"}
            or d["policy"] != POLICY
            or d["prime"] != prime
            or d["year"] != year
        ):
            raise ValueError()
        if (
            not isinstance(d["statement"], str)
            or not d["statement"]
            or not isinstance(d["allocations"], list)
            or len(d["allocations"]) != 12
        ):
            raise ValueError()
        for ref in [d["expenses"], *d["allocations"]]:
            if ref is not None and (
                not isinstance(ref, str)
                or len(ref) != 64
                or any(c not in "0123456789abcdef" for c in ref)
            ):
                raise ValueError()
        if d["expenses"] is None:
            raise ValueError()
        return d
    except (ValueError, TypeError, KeyError):
        raise ValueError("Invalid margins selection") from None


def ratio(numerator, denominator):
    result = dict(
        value=None,
        status="unavailable",
        unit="monthly_fraction",
        reason="incomplete_numerator_or_denominator",
    )
    if numerator["status"] != "available" or denominator["status"] != "available":
        return result
    with localcontext() as ctx:
        ctx.prec = 60
        n, d = Decimal(numerator["value"]), Decimal(denominator["value"])
        if not n.is_finite() or not d.is_finite():
            raise ValueError("Nonfinite margin input")
        if d <= 0:
            return {**result, "status": "not_applicable", "reason": "nonpositive_denominator"}
        return {**result, "value": str(n / d), "status": "available", "reason": None}


def allocation_slice(doc, month):
    """Disjoint priced rows only. Uncovered rows and reconciliation stay explicit."""
    if doc is None:
        return dict(
            status="unavailable",
            reason="allocation_publication_missing",
            included_venues=[],
            excluded_venues=[],
            metrics={},
        )
    if doc["prime"] != "spark" or doc["month"] != month:
        raise ValueError("Allocation publication perimeter differs")
    seen, included, excluded = set(), [], []
    totals = {k: Decimal(0) for k in ("assets", "revenue", "cost")}
    with localcontext() as ctx:
        ctx.prec = 60
        for row in doc["allocations"]:
            key = row["venue_id"]
            if key in seen:
                raise ValueError("Duplicate allocation")
            seen.add(key)
            values = [
                row.get(k)
                for k in (
                    "average_yield_exposure_usd",
                    "prime_retained_revenue_usd",
                    "borrowing_cost_usd",
                )
            ]
            if row.get("cost_reported_under") or any(v is None for v in values):
                excluded.append(
                    dict(
                        venue=key,
                        reason="cost_reported_elsewhere"
                        if row.get("cost_reported_under")
                        else "missing_amount_or_noninvestment_row",
                    )
                )
                continue
            a, r, c = map(Decimal, values)
            if not all(v.is_finite() for v in (a, r, c)) or a < 0 or c < 0:
                raise ValueError("Invalid allocation input")
            if a == 0:
                excluded.append(dict(venue=key, reason="no_positive_average_exposure"))
                continue
            included.append(key)
            totals["assets"] += a
            totals["revenue"] += r
            totals["cost"] += c
        cells = {k: metric(v) for k, v in totals.items()}
        net = metric(totals["revenue"] - totals["cost"])
    if not included:
        return dict(
            status="unavailable",
            reason="no_covered_allocations",
            included_venues=[],
            excluded_venues=excluded,
            metrics={},
        )
    return dict(
        status="partial",
        reason="covered_allocations_only_not_statement_perimeter",
        included_venues=included,
        excluded_venues=excluded,
        amounts=cells,
        metrics=dict(
            yield_=ratio(cells["revenue"], cells["assets"]),
            cost_on_assets=ratio(cells["cost"], cells["assets"]),
            nim=ratio(net, cells["assets"]),
        ),
        allocation_revision=doc["revision_id"],
        reconciliation=deepcopy(doc.get("reconciliation", {})),
        limitations=deepcopy(doc.get("limitations", [])),
    )


def project(statement, allocations, expense_input):
    if (
        statement["prime"] != "spark"
        or statement["year"] != 2026
        or not statement.get("review_policy")
    ):
        raise ValueError("Margins require Spark 2026 reviewed reporting statements")
    lookup = {row["id"]: row for row in statement["lines"]}
    months, allocation_refs = [], []
    for period, doc in zip(statement["periods"], allocations, strict=True):
        month = period["key"]
        end = f"{month}-{calendar.monthrange(2026, int(month[5:]))[1]:02}"
        if doc is not None and (
            (period.get("expected_cutoff") or "") < end or doc.get("period", {}).get("end") != end
        ):
            raise ValueError("Allocation and statement periods differ")
        allocation_refs.append(doc["revision_id"] if doc else None)
        expense = expenses_month(statement, period, expense_input)

        def cell(key, month=month):
            return (
                deepcopy(lookup[key]["values"][month])
                if key in lookup
                else metric(reason="statement_component_missing")
            )

        r, c = cell("statement.interest"), cell("statement.funding")
        net_interest, net_revenue = cell("statement.net_interest"), cell("statement.net_revenue")
        direct = combine([(k, 1, expense[k]) for k in ("expenses.distribution", "expenses.other")])
        before = combine(
            [("statement.net_revenue", 1, net_revenue), ("direct_expenses", -1, direct)]
        )
        after = combine(
            [("nrm_before", 1, before), ("expenses.incentives", -1, expense["expenses.incentives"])]
        )
        net = combine(
            [
                ("statement.net_revenue", 1, net_revenue),
                ("expenses.total", -1, expense["expenses.total"]),
            ]
        )
        covered = allocation_slice(doc, month)
        # Closing B/S stocks and original investment principal are not monthly
        # average assets/debt. Do not manufacture these from mismatched snapshots.
        a = metric(reason="statement_perimeter_average_assets_not_published")
        if covered.get("amounts"):
            a.update(status="partial", known_value=covered["amounts"]["assets"]["value"])
        f = metric(reason="average_actual_Sky_debt_plus_savings_liabilities_not_published")
        numerators = dict(
            investment_income=r,
            funding_expense=c,
            net_interest=net_interest,
            other_income=cell("statement.noninterest_income"),
            direct_expenses=direct,
            user_incentives=expense["expenses.incentives"],
            operating_expenses=expense["expenses.operating"],
            nrm_before_incentives=before,
            nrm_after_incentives=after,
            net_income=net,
        )
        metrics = dict(
            allocation_yield=ratio(r, a),
            cost_on_assets=ratio(c, a),
            funding_rate=ratio(c, f),
            nis=dict(
                value=None,
                status="unavailable",
                unit="monthly_fraction",
                reason="matching_average_assets_and_funding_missing",
            ),
            nim=ratio(net_interest, a),
            nrm_before_incentives=ratio(before, a),
            nrm_after_incentives=ratio(after, a),
            nm=ratio(net, a),
        )
        months.append(
            dict(
                month=month,
                period=period,
                numerators=numerators,
                denominators=dict(average_allocated_assets=a, average_funding=f),
                metrics=metrics,
                allocation_slice=covered,
                expense_basis=expense_input["snapshot"]["operating_basis"].get(month),
            )
        )
    selection = encode(
        dict(
            policy=POLICY,
            prime="spark",
            year=2026,
            statement=statement["selection"],
            allocations=allocation_refs,
            expenses=expense_input["revision_id"],
        )
    )
    result = dict(
        schema_version="reporting-margins.v1",
        policy_version=POLICY,
        prime="spark",
        year=2026,
        scope="individual_operational_prime_matching_reviewed_statement",
        as_of=statement["as_of"],
        provisional=True,
        months=months,
        selection=selection,
        statement_report_hash=statement["report_hash"],
        statement_selection=statement["selection"],
        expense_revision=expense_input["revision_id"],
        source_manifest=deepcopy(statement["source_manifest"]),
        methodology=dict(
            frequency="monthly_not_annualized",
            expense_status="provisional_unverified",
            expense_classification="direct = distribution + other; incentives separate; operating includes contributor compensation once",
            net_margin="management_net_return_on_assets_not_revenue_margin_or_tax_certification",
            reconciliation="deferred_not_redistributed; full Spark numerators use statement funding, not allocation cost subtotal",
            denominator="covered venue averages disclosed separately; not certified full Spark average assets or average funding",
        ),
    )
    result["report_hash"] = digest(result)
    return result
