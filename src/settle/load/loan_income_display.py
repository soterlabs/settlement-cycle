"""Use retained Anchorage receipt attribution in human-readable reports only."""

from copy import deepcopy
from decimal import Decimal, localcontext

PAYER = "0x49506c3aa028693458d6ee816b2ec28522946872"
NOTE = (
    "Anchorage interest received at the ALM is attributed to S23 and removed from S26 once. "
    "S23 balance columns show escrow cash, not outstanding loan principal. "
    "S26 retains other ALM USDC balances and any other income. "
    "Borrowing-cost allocation and aggregate settlement amounts are unchanged."
)


def receipt_amount(prov):
    if prov.get("prime_id") != "spark":
        return None
    views = (prov.get("venue_views") or {}).get("settlement", [])
    matches = [r for r in views if r.get("venue_id") in {"S23", "S26"}]
    by_id = {r["venue_id"]: r for r in matches}
    if len(by_id) != len(matches):
        raise ValueError("Duplicate Anchorage display attribution")
    if set(by_id) != {"S23", "S26"}:
        return None  # Legacy reports need retained payer attribution; never guess from S26 total.
    loan, cash = by_id["S23"], by_id["S26"]
    if any(r.get("status") != "available" or not r.get("enabled") for r in matches):
        return None
    raw = {r["venue_id"]: r for r in prov["venue_breakdown"]}
    if not {"S23", "S26"} <= raw.keys():
        raise ValueError("Anchorage attribution has no accounting rows")
    if (
        loan.get("method") != "alm_receipts"
        or loan.get("receipt_attribution") != {"source_venue": "S26", "payer": PAYER}
        or cash.get("income_attributed_to") != ["S23"]
    ):
        raise ValueError("Unsupported Anchorage attribution route")
    with localcontext() as ctx:
        ctx.prec = 100
        amount = Decimal(loan["cash_received_usd"])
        if not amount.is_finite() or (
            Decimal(loan["income_usd"]) != Decimal(raw["S23"]["revenue"]) + amount
            or Decimal(cash["income_usd"]) != Decimal(raw["S26"]["revenue"]) - amount
            or Decimal(cash["income_before_attribution_usd"]) != Decimal(raw["S26"]["revenue"])
        ):
            raise ValueError("Anchorage attribution does not reconcile")
    return amount


def display_rows(prov, rows, *, workbook=False):
    """Reclassify income after CoF computation; never mutate retained inputs."""
    result = deepcopy(rows)
    amount = receipt_amount(prov)
    if amount is None:
        return result
    by_id = {r["venue_id"]: r for r in result}
    actual = "actual_rev" if workbook else "actual_revenue"
    with localcontext() as ctx:
        ctx.prec = 100
        for key, sign in (("S23", 1), ("S26", -1)):
            row = by_id[key]
            for field in (actual, "revenue", *(["profit_to_grove"] if workbook else [])):
                value = Decimal(str(row[field])) + sign * amount
                row[field] = value if workbook else str(value)
            row["label"] = (
                "Anchorage loan — interest receipts (balances: escrow only)"
                if key == "S23"
                else "USDC raw (ALM idle)"
            )
            if workbook:
                row["note"] = (row.get("note", "") + " " + NOTE).strip()
    return result
