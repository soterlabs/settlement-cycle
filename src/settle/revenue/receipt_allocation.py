"""Reporting-only allocation of received income within its receipt month.

This is an estimated daily presentation of cash income, never an accrual of
unpaid earnings. Existing statements, actual receipts and MSC are unchanged.
"""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext

from .financial import metric, number
from .financial_allocations import ALLOCATIONS
from .financial_spark import RESERVE_RECEIPTS, UNMAPPED_RECEIPTS
from .verification import digest

POLICY = "spark.receipt-month-allocation.v1"
METHOD = "equal_daily_allocation_within_observed_receipt_month"
STREAMS = {
    "investment.anchorage": ("S23", "Anchorage interest received"),
    "rewards.issuer.usdg": ("S69", "USDG issuer incentives"),
    **{
        key: (venue, "SparkLend " + symbol.upper() + " fees received")
        for key, venue, symbol in zip(
            (*RESERVE_RECEIPTS, *UNMAPPED_RECEIPTS),
            ("S1", "S2", "S3", "S4", "S5", "S70", "S71"),
            ("usds", "usdc", "usdt", "dai", "pyusd", "rlusd", "usdg"),
            strict=True,
        )
    },
    **{
        "rewards.lending." + spec.key: (spec.venue, spec.label.replace("supply yield", "external rewards received"))
        for spec in ALLOCATIONS["spark"] if spec.family == "Aave v3"
    },
    "investment.rwa.buidl": ("S19", "BUIDL distributions (dormant)"),
    "rewards.unattributed_sparklend_receipts": (None, "Unattributed SparkLend receipts"),
}


def equal_days(amount, count):
    """Exact decimal allocation; put only the subprecision residual on last day."""
    exponent = min(-18, amount.as_tuple().exponent - 18)
    precision = max(80, len(amount.as_tuple().digits) + max(amount.as_tuple().exponent, 0) + abs(exponent) + 20)
    with localcontext() as ctx:
        ctx.prec = precision
        units = int(abs(amount).scaleb(-exponent))
        base, residual = divmod(units, count)
        sign = -1 if amount < 0 else 1
        return [str(Decimal(sign * (base + (residual if i == count - 1 else 0))).scaleb(exponent))
                for i in range(count)]


def coverage_end(cell, document, month, expected):
    end = cell.get("period_end")
    refs = [*cell.get("source_refs", []), cell.get("source_ref")]
    ends = [document["source_manifest"][ref]["cutoff"] for ref in refs
            if ref in document["source_manifest"]]
    if end is None:
        end = min(ends) if ends else None
    elif ends and end > min(ends):
        return None
    if end is None:
        return None
    try:
        day = date.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    return day if str(day)[:7] == month and day <= expected else None


def dated_receipts(key, monthly, components, start, end):
    """Disclose original dates only when the retained ledger proves the total."""
    if key != "investment.anchorage" or end is None:
        return {"status": "unavailable", "reason": "dated_receipts_not_in_retained_source", "rows": None}
    for publication in components:
        source = publication["document"].get("statement_allocations", {}).get("anchorage.receipts", {})
        rows = source.get("daily_receipts")
        if (source.get("status") not in ("available", "partial") or not isinstance(rows, list)
                or source.get("period_end", publication["cutoff"]) != str(end)):
            continue
        try:
            parsed = [{"date": row["date"], "income_usd": str(number(row["income_usd"]))} for row in rows]
            if any(not start <= date.fromisoformat(row["date"]) <= end for row in parsed):
                continue
            with localcontext() as ctx:
                ctx.prec = 100
                if sum((number(row["income_usd"]) for row in parsed), Decimal(0)) != number(monthly):
                    continue
        except (KeyError, TypeError, ValueError):
            continue
        return {"status": "available", "source_ref": publication["revision_id"], "rows": parsed}
    return {"status": "unavailable", "reason": "dated_receipts_not_reconciled_to_selected_amount", "rows": None}


def project(document, month, stream=None, *, components=()):
    if document["prime"] != "spark":
        raise ValueError("Receipt allocation currently supports Spark only")
    period = next(p for p in document["periods"] if p["key"] == month)
    if period["state"] == "future":
        raise ValueError("Selected report does not cover this month")
    if stream is not None and stream not in STREAMS:
        raise KeyError(stream)
    first, expected = date.fromisoformat(month + "-01"), date.fromisoformat(period["expected_cutoff"])
    dates = [first + timedelta(days=i) for i in range((expected - first).days + 1)]
    lookup = {row["id"]: row for row in document["lines"]}
    result = []
    for key, (venue, label) in STREAMS.items():
        if stream is not None and key != stream:
            continue
        source = deepcopy(lookup[key]["values"][month]) if key in lookup else metric(reason="receipt_stream_not_published")
        value = source["value"] if source["status"] == "available" else source.get("known_value")
        cash = source.get("recognition") in ("receipt", "receipt_in_kind")
        # BUIDL is dormant and the legacy statement has a generic return label.
        # Zero is immaterial to basis; never reinterpret a future nonzero NAV
        # return as cash without explicit receipt recognition.
        dormant = (key == "investment.rwa.buidl" and value is not None and number(value) == 0
                   and source.get("recognition") == "investment_return")
        end = coverage_end(source, document, month, expected) if value is not None and (cash or dormant) else None
        count = (end - first).days + 1 if end else 0
        amounts = equal_days(number(value), count) if count else []
        recognition = source.get("recognition") if cash else "receipt"
        daily = []
        for i, day in enumerate(dates):
            item = metric(amounts[i] if i < count and source["status"] == "available" else None,
                          reason="source_does_not_cover_day", basis=METHOD)
            if i < count and source["status"] != "available":
                item.update(status="partial", known_value=amounts[i], reason=source.get("reason"))
            item.update(date=str(day), quality="estimate", recognition=recognition)
            daily.append(item)
        received = deepcopy(source)
        if dormant:
            received.update(recognition="receipt", quality="estimate", basis="dormant_BUIDL_zero_receipts")
        elif value is not None and not cash:
            received = metric(reason="explicit_receipt_recognition_required")
        result.append({
            "metric_id": key, "venue_id": venue, "label": label,
            "source_monthly_metric": source, "monthly_receipts": received,
            "allocation_method": METHOD, "quality": "estimate", "recognition": recognition,
            "allocation_status": ("available" if source["status"] == "available" and end == expected else "partial") if count else "unavailable",
            "classification": "explicit_receipt_metric" if cash else "dormant_zero_only" if dormant else "receipt_basis_not_established",
            "period_start": str(first), "allocated_through": str(end) if end else None,
            "allocated_days": count, "daily": daily,
            "actual_receipts": dated_receipts(key, value, components, first, end),
        })
    body = {
        "schema_version": "1.0", "policy_version": POLICY,
        "prime": "spark", "month": month, "period": deepcopy(period),
        "statement_policy_version": document["policy_version"],
        "selection": document["selection"], "source_report_hash": document["report_hash"],
        "source_manifest": deepcopy(document["source_manifest"]),
        "accounting_basis": "cash", "true_accrual": False, "principal_included": False,
        "rounding": "exact_decimal_total_with_last_observed_day_residual",
        "streams": result,
    }
    body["projection_hash"] = digest(body)
    body["source_lag_periods"] = document.get("source_lag_periods", [])
    return body
