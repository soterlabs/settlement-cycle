"""Versioned monthly allocation analytics; never an input to settlement charges.

Publication consumes reviewed offline artifacts. API reads make no provider
calls, run no capital replay, and never infer a missing cost to be zero.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..compute.allocation_financing import annualized_yield
from .verification import PRIMES, canonical, digest

ROOT = Path(__file__).resolve().parents[3]


def month_bounds(month: str) -> tuple[date, date]:
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("month must be YYYY-MM")
    start = date.fromisoformat(month + "-01")
    return start, start.replace(day=calendar.monthrange(start.year, start.month)[1])


def number(value, *, nonnegative=False):
    """Public decimals are strings, including tiny negative yields."""
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid allocation decimal") from exc
    if not result.is_finite() or (nonnegative and result < 0):
        raise ValueError("Invalid allocation decimal")
    return str(result)


def period_return(profit, average):
    if profit is None or average is None or Decimal(average) == 0:
        return None
    return str(Decimal(profit) / Decimal(average))


def indexed(rows, key):
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError("Duplicate allocation or prime")
    return result


def build_documents(readiness, yields, *, source_hashes):
    """Join matching capital/cost and yield artifacts into monthly documents.

    Yield returns are ratios (0.05 = 5%). Monthly returns are unannualized;
    APYs use the existing annualization method. Cash-recognized revenue is
    reported as booked, not silently converted into contractual accrual.
    """
    capital = indexed(readiness["results"], "prime")
    yield_primes = indexed(yields["results"], "prime")
    if set(capital) != set(yield_primes):
        raise ValueError("Capital and yield prime coverage differs")
    if not source_hashes or any(
        not re.fullmatch("[0-9a-f]{64}", h) for h in source_hashes.values()
    ):
        raise ValueError("Source hashes are required")
    documents = []
    for prime, c in capital.items():
        y = yield_primes[prime]
        if prime not in PRIMES:
            raise ValueError("Unknown prime")
        month = c["start"][:7]
        start, end = month_bounds(month)
        if any(r["start"] != str(start) or r["end"] != str(end) for r in (c, y)):
            raise ValueError("Inputs must cover the same complete calendar month")
        costs, revenues = indexed(c["allocations"], "venue"), indexed(y["allocations"], "venue")
        if costs.keys() != revenues.keys():
            raise ValueError("Capital and yield allocation coverage differs")
        rows = []
        for venue, f in costs.items():
            r = revenues[venue]
            cost = number(f["cost_of_funds"], nonnegative=True)
            if (
                cost != number(r["cost_of_funds"], nonnegative=True)
                or f["status"] != r["cost_status"]
            ):
                raise ValueError("Yield uses a different borrowing-cost snapshot")
            known_cost_statuses = {
                "calculable_under_assumption", "excluded_from_cost", "no_principal_in_period"
            }
            if (f["status"] in known_cost_statuses) != (cost is not None):
                raise ValueError("Borrowing cost availability contradicts its status")
            if f["status"] in {"excluded_from_cost", "no_principal_in_period"} and Decimal(cost) != 0:
                raise ValueError("Excluded or zero-principal allocation must have zero cost")
            gross, net = number(r.get("gross_revenue")), number(r.get("net_pnl"))
            average = number(r["average_exposure"], nonnegative=True)
            gross_apy, net_apy = number(r["gross_apy"]), number(r["net_apy"])
            if cost is None and (net is not None or net_apy is not None):
                raise ValueError("Unavailable cost cannot produce net yield")
            retained = number(r.get("prime_retained_revenue"))
            if retained is not None and retained != number(f.get("revenue")):
                raise ValueError("Yield uses different retained revenue")
            if net is not None and (
                retained is None or Decimal(net) != Decimal(retained) - Decimal(cost)
            ):
                raise ValueError("Net PnL does not match revenue minus borrowing cost")
            for profit, apy in ((gross, gross_apy), (net, net_apy)):
                expected = (
                    annualized_yield(Decimal(profit), Decimal(average), (end - start).days + 1)
                    if profit is not None and average is not None
                    else None
                )
                if (expected is None) != (apy is None) or (
                    expected is not None and abs(expected - Decimal(apy)) > Decimal("1e-25")
                ):
                    raise ValueError("Yield does not match its revenue and exposure")
            row = {
                "venue_id": venue,
                "label": f["label"],
                "average_original_principal_usd": number(
                    f.get("average_principal"), nonnegative=True
                ),
                "closing_original_principal_usd": number(
                    f.get("closing_principal"), nonnegative=True
                ),
                "average_yield_exposure_usd": average,
                "yield_denominator": r["denominator"],
                "borrowing_cost_usd": cost,
                "borrowing_cost_status": f["status"],
                "gross_revenue_usd": gross,
                "prime_retained_revenue_usd": retained,
                "net_pnl_usd": net,
                "gross_monthly_return": period_return(gross, average),
                "net_monthly_return": period_return(net, average),
                "gross_apy": gross_apy,
                "net_apy": net_apy,
                "yield_status": r["yield_status"],
                "excess_deduction_credit_usd": number(
                    f.get("excess_deduction_credit"), nonnegative=True
                ),
                "spread_reimbursement_excluded_from_net_usd": number(
                    r.get("spread_reimbursement_not_in_venue_net")
                ),
                "savings_dependency_days": f.get("savings_dependency_days", []),
                "cost_reported_under": f.get("cost_reported_under"),
            }
            rows.append(row)
        documents.append(
            {
                "schema_version": "1.0",
                "prime": prime,
                "month": month,
                "cadence": "monthly",
                "period": {"start": str(start), "end": str(end), "days": (end - start).days + 1},
                "provisional": True,
                "scope": "allocation analytics; not a settlement restatement",
                "units": {"amounts": "USD", "returns": "decimal ratio; 0.05 means 5%"},
                "funding_policy": c["policy"],
                "capital_method": c["method"],
                "apy_method": yields["method"],
                "net_definition": yields["net_definition"],
                "limitations": c["limitations"],
                "source_hashes": source_hashes,
                "count": len(rows),
                "allocations": rows,
            }
        )
    return documents


def apply_schema(conn):
    conn.execute((ROOT / "db/schema_allocation_monthly.sql").read_text())


def publish(conn, document):
    """Caller owns transaction; old revisions remain queryable."""
    start, _ = month_bounds(document["month"])
    if document["prime"] not in PRIMES or document.get("provisional") is not True:
        raise ValueError("Only provisional allocation analytics can be published here")
    revision = digest(document)
    conn.execute(
        """INSERT INTO allocation_monthly_results (revision_id, prime, month, document)
                    VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        (revision, document["prime"], start, Jsonb(document)),
    )
    return revision


def read(conn, prime, month, *, revision=None):
    start, _ = month_bounds(month)
    clauses, params = ["prime=%s", "month=%s"], [prime, start]
    if revision is not None:
        clauses.append("revision_id=%s")
        params.append(revision)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT revision_id, computed_at, document FROM allocation_monthly_results WHERE "
            + " AND ".join(clauses)
            + " ORDER BY publication_order DESC LIMIT 1",
            params,
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        **row["document"],
        "revision_id": row["revision_id"],
        "published_at": canonical(row["computed_at"]),
    }
