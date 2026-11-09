"""Versioned authored expense inputs, independently pinned in report selections."""

import json
from importlib.resources import files

from .verification import digest

INITIAL_CONTRIBUTOR_INPUT = "3fc8771ad45abee33ce27f2e50c188f368b67d43661f49b7f4b8b861e316774c"


def contributor_input(revision=None):
    root = files("settle.revenue")
    if revision is None:
        current = json.loads(root.joinpath("contributor_compensation.json").read_text())
        revision = digest(current)
    if (
        not isinstance(revision, str)
        or len(revision) != 64
        or any(c not in "0123456789abcdef" for c in revision)
    ):
        raise ValueError("Invalid expense input revision")
    try:
        result = json.loads(root.joinpath("published_expenses", revision + ".json").read_text())
    except FileNotFoundError:
        raise ValueError("Archive the approved expense input before publishing") from None
    if digest(result) != revision or result.get("prime") != "spark" or result.get("year") != 2026:
        raise ValueError("Expense input provenance differs")
    if set(result["months"]) != {f"2026-{m:02}" for m in range(1, 13)}:
        raise ValueError("Expense input months differ")
    from decimal import Decimal

    for value in result["months"].values():
        if value is not None and (not isinstance(value, str) or not Decimal(value).is_finite()):
            raise ValueError("Expense input amount is invalid")
    schedule = result.get("schedule")
    if schedule:
        from decimal import localcontext
        with localcontext() as ctx:
            ctx.prec = 100
            monthly = Decimal(schedule["monthly_usd"])
            if (monthly <= 0 or Decimal(schedule["tokens"]) * Decimal(schedule["grant_price_usd"]) / 48 != monthly
                    or any(value is None or Decimal(value) != monthly for value in result["months"].values())):
                raise ValueError("Contributor grant schedule does not reconcile")
    return result, revision
