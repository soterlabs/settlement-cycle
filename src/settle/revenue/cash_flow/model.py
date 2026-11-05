"""Exact cash-flow metrics and deterministic subtotal/coverage semantics."""

from copy import deepcopy
from decimal import Decimal, localcontext

from ..verification import digest


def amount(cell):
    if not cell:
        return None
    raw = cell.get("value") if cell.get("value") is not None else cell.get("known_value")
    return Decimal(raw) if raw is not None else None


def metric(
    value=None, *, complete=True, recognition="receipt", quality="supported", reason=None, refs=()
):
    value = str(value) if value is not None else None
    return {
        "value": value if complete else None,
        "known_value": value if not complete else None,
        "status": "available"
        if value is not None and complete
        else "partial"
        if value is not None
        else "unavailable",
        "unit": "USD",
        "recognition": recognition,
        "quality": quality,
        "basis": "event_backed_management_cash_flow",
        "reason": reason,
        "source_paths": list(refs),
    }


def total(terms, *, reason="Missing cash-flow inputs"):
    known = [(name, sign, c) for name, sign, c in terms if amount(c) is not None]
    complete = bool(terms) and all(c.get("status") == "available" for _, _, c in terms)
    with localcontext() as ctx:
        ctx.prec = 100
        value = sum((amount(c) * sign for _, sign, c in known), Decimal(0)) if known else None
    recognitions = {c.get("recognition") for _, _, c in known}
    return {
        **metric(
            value,
            complete=complete,
            recognition=next(iter(recognitions))
            if len(recognitions) == 1
            else "mixed_management_components",
            quality="estimate"
            if any(c.get("quality") == "estimate" for _, _, c in known)
            else "supported",
            reason=None if complete else reason,
            refs=sorted({r for _, _, c in known for r in c.get("source_paths", [])}),
        ),
        "components": [{"id": n, "sign": s} for n, s, _ in terms],
        "missing_components": [n for n, _, c in terms if c.get("status") != "available"],
    }


CHILDREN = {
    "non-cash": ["token-incentives", "non-stable-gains", "non-stable-transfers", "other-non-cash"],
    "receivables": [
        "earned-not-received",
        "sky-allocation-receipts",
        "sky-demand-receipts",
        "external-receipts",
    ],
    "payables": ["expenses-booked", "expenses-paid"],
    "net-operating": ["net-income", "non-cash", "receivables", "payables"],
    "net-investing": ["allocation-changes", "investments"],
    "net-financing": ["usds-drawn", "deposits", "sky-contributions", "buybacks"],
    "net-cash-change": ["net-operating", "net-investing", "net-financing"],
}


def subtotals(metrics):
    for parent, children in CHILDREN.items():
        metrics[parent] = total([(key, 1, metrics[key]) for key in children])
    return metrics


def validate(document):
    if (
        document.get("schema_version") != "cash-flow-month.v1"
        or document.get("prime") != "spark"
        or document["period_start"] != document["cutoff"][:7] + "-01"
    ):
        raise ValueError("Cash-flow identity differs")
    metrics = document["metrics"]
    for cell in metrics.values():
        for field in ["value", "known_value"]:
            v = cell.get(field)
            if v is not None and (not isinstance(v, str) or not Decimal(v).is_finite()):
                raise ValueError("Invalid cash-flow decimal")
        if cell["status"] == "available" and (
            cell.get("value") is None or cell.get("known_value") is not None
        ):
            raise ValueError("Complete cash-flow metric lacks a value")
        if cell["status"] == "partial" and (
            cell.get("value") is not None or cell.get("known_value") is None
        ):
            raise ValueError("Partial cash-flow coverage differs")
    expected = subtotals(deepcopy(metrics))
    if any(metrics[k] != expected[k] for k in CHILDREN):
        raise ValueError("Cash-flow subtotal differs")
    if document["event_digest"] != digest(document["event_summaries"]):
        raise ValueError("Cash-flow evidence summary differs")
    return document
