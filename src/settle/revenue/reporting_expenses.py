"""Versioned provisional expense input shared by statements and margins.

Imported without changing the reporting-dashboard workbook assumptions. This is
not a settlement expense or an assertion that budget values are actuals.
"""

import hashlib
import json
from copy import deepcopy
from decimal import Decimal
from importlib.resources import files

from .financial import metric
from .statement_rollups import combine

ROOTS = ("expenses.operating", "expenses.incentives", "expenses.distribution", "expenses.other")


def snapshot(prime, year, revision=None):
    if (prime, year) != ("spark", 2026):
        raise ValueError("Expense input currently supports Spark 2026")
    data = json.loads(files("settle.revenue").joinpath("spark_expenses_2026.v1.json").read_text())
    # Same ordered JSON serialization as the original dashboard snapshot hash.
    ref = hashlib.sha256(
        json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    if revision is not None and revision != ref:
        raise ValueError("Unknown expense input revision")
    lookup = {r["id"]: r for r in data["lines"]}
    if len(lookup) != len(data["lines"]):
        raise ValueError("Duplicate expense line")
    for row in data["lines"]:
        children = (
            [lookup[k] for k in ROOTS]
            if row["id"] == "expenses.total"
            else [x for x in data["lines"] if x["parent"] == row["id"]]
        )
        for month, value in row["values"].items():
            if value is not None and not Decimal(value).is_finite():
                raise ValueError("Nonfinite expense")
            if children:
                complete = all(x["values"][month] is not None for x in children)
                if complete != (value is not None):
                    raise ValueError("Expense subtotal coverage differs")
                if complete and abs(
                    sum(Decimal(x["values"][month]) for x in children) - Decimal(value)
                ) > Decimal(".01"):
                    raise ValueError("Expense subtotal differs")
    return dict(schema_version="reporting-expenses.v1", revision_id=ref, snapshot=data)


def monthly(statement, period, source):
    """Sum disjoint leaves, including API contributor compensation exactly once."""
    month = period["key"]
    import calendar

    end = f"{month}-{calendar.monthrange(int(month[:4]), int(month[5:]))[1]:02}"
    data, ref = source["snapshot"], source["revision_id"]
    rows = {r["id"]: deepcopy(r) for r in data["lines"]}
    contributor = next(
        (r for r in statement["lines"] if r["id"] == "expenses.compensation.spk"), None
    )
    if contributor and not data.get("includes_contributor_compensation"):
        rows[contributor["id"]] = dict(
            id=contributor["id"], parent="expenses.compensation", values={}
        )
    memo = {}

    def build(key):
        if key in memo:
            return memo[key]
        row = rows[key]
        children = (
            list(ROOTS)
            if key == "expenses.total"
            else [k for k, v in rows.items() if v["parent"] == key]
        )
        if period["state"] == "future" or (period.get("expected_cutoff") or "") < end:
            cell = metric(reason="monthly_expense_after_selected_cutoff")
        elif children:
            cell = combine([(k, 1, build(k)) for k in children])
        elif contributor and key == contributor["id"]:
            cell = deepcopy(contributor["values"][month])
        else:
            cell = metric(
                row["values"].get(month),
                reason="expense_not_provided",
                basis="provisional_workbook_expense",
            )
            cell.update(
                quality="estimate",
                source_refs=[ref],
                period_start=month + "-01",
                period_end=end,
                source_paths=[f"spark_expenses_2026.v1.json#{key}/{month}"],
            )
        memo[key] = cell
        return cell

    for key in rows:
        build(key)
    # Always keep provisional budgeting/estimation visible, including subtotals.
    for cell in memo.values():
        cell["quality"] = "estimate"
    return memo
