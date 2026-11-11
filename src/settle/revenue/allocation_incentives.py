"""Accrual-only allocation presentation. Never used by MSC settlement compute."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal as D
from decimal import localcontext

from ..compute.allocation_financing import annualized_yield
from ..compute.incentives import EligibleBalance, daily_accrual
from .allocations import indexed, period_return

MAPPING = {
    "rlusd": {"S64": "S64", "lending_idle": "S70", "S66": "S66", "morpho_top_up": "S72"},
    "pyusd": {"S28": "S28", "lending_idle": "S5", "S61": "S61", "S25": "S25"},
}
CASH_VENUES = {"rlusd": "S64", "pyusd": "S28"}
TOLERANCE = D("1e-18")


def close(actual, expected):
    a, b = D(actual), D(expected)
    if not a.is_finite() or not b.is_finite() or abs(a - b) > TOLERANCE:
        raise ValueError("Retained incentive amount differs from recomputed accrual")


def component_accruals(inputs):
    start, end = (date.fromisoformat(inputs["period"][k]) for k in ("start", "end"))
    expected_days = [str(start + timedelta(days=i)) for i in range((end - start).days + 1)]
    if set(inputs["programs"]) != set(MAPPING) or set(inputs["reference_rates"]) != set(
        expected_days
    ):
        raise ValueError("Incomplete program/reference-rate coverage")
    allocated, reconciliations = {}, {}
    with localcontext() as ctx:
        ctx.prec = 60
        for program, source in inputs["programs"].items():
            if (
                source["status"] != "available"
                or source["recognition"] != "accrual"
                or source["program"] != program
            ):
                raise ValueError("Unavailable/non-accrual issuer input")
            if [r["date"] for r in source["daily"]] != expected_days:
                raise ValueError("Incomplete/duplicate daily accrual coverage")
            totals = defaultdict(lambda: D(0))
            for row in source["daily"]:
                day = date.fromisoformat(row["date"])
                components = indexed(row["components"], "component")
                expected = set(MAPPING[program])
                if program == "pyusd" and day > date(2026, 6, 30):
                    expected.remove("S25")
                if set(components) != expected:
                    raise ValueError("Incorrect eligible component coverage")
                eligible = []
                for name, c in components.items():
                    top_up = name == "morpho_top_up"
                    if top_up != (c["net_vault_apr"] is not None):
                        raise ValueError("Missing/unexpected fee-net vault APR")
                    eligible.append(
                        EligibleBalance(
                            name, D(c["balance"]), D(c["net_vault_apr"]) if top_up else None
                        )
                    )
                earned, rebuilt = daily_accrual(
                    program,
                    day,
                    eligible,
                    D(inputs["reference_rates"][row["date"]]) if program == "pyusd" else None,
                )
                close(earned, row["accrual"])
                for c in rebuilt:
                    close(c["apr"], components[c["component"]]["apr"])
                    close(c["accrual"], components[c["component"]]["accrual"])
                    totals[MAPPING[program][c["component"]]] += D(c["accrual"])
            total = sum(totals.values(), D(0))
            close(total, source["value"])
            paid = D(source["period_receipts_usd"])
            if not paid.is_finite() or paid < 0:
                raise ValueError("Invalid issuer cash receipts")
            opening = inputs["opening_receivable"].get(program)
            if opening is not None and not D(opening).is_finite():
                raise ValueError("Invalid opening receivable/prepayment")
            reconciliations[program] = {
                "accrued_usd": str(total),
                "cash_received_usd": str(paid),
                "receivable_change_usd": str(total - paid),
                "opening_receivable_usd": opening,
                "closing_receivable_usd": str(D(opening) + total - paid)
                if opening is not None
                else None,
                "status": "reconciled" if opening is not None else "opening_balance_unverified",
                "allocated_by_venue_usd": {k: str(v) for k, v in totals.items()},
            }
            allocated[program] = totals
    return allocated, reconciliations


def apply_accruals(document, inputs, *, cash_included):
    """Replace ONLY identified issuer cash with accrual; retain investment income."""
    if document["prime"] != "spark" or any(
        document["period"][k] != inputs["period"][k] for k in ("start", "end")
    ):
        raise ValueError("Incentive period/prime differs")
    if "issuer_incentive_reconciliation" in document:
        raise ValueError("Incentives already attributed")
    if set(cash_included) != set(MAPPING):
        raise ValueError("Explicit cash inclusion is required")
    allocated, reconciliation = component_accruals(inputs)
    rows = indexed(document["allocations"], "venue_id")
    for program, amounts in allocated.items():
        cash_venue = CASH_VENUES[program]
        close(cash_included[program], inputs["programs"][program]["period_receipts_usd"])
        for venue, earned in amounts.items():
            row = rows[venue]
            removed = D(cash_included[program]) if venue == cash_venue else D(0)
            # Base revenues retain normal investment earnings (including the
            # vault's fee-net return); only issuer cash is replaced.
            investment = D(row["gross_revenue_usd"]) - removed
            retained = D(row["prime_retained_revenue_usd"]) - removed
            gross, kept = investment + earned, retained + earned
            average, cost = D(row["average_yield_exposure_usd"]), D(row["borrowing_cost_usd"])
            if (
                not all(v.is_finite() for v in (gross, kept, average, cost))
                or average < 0
                or cost < 0
            ):
                raise ValueError("Invalid allocation inputs")
            net = kept - cost
            row.update(
                gross_revenue_usd=str(gross),
                prime_retained_revenue_usd=str(kept),
                net_pnl_usd=str(net),
                gross_monthly_return=period_return(gross, average),
                net_monthly_return=period_return(net, average),
                gross_apy=str(annualized_yield(gross, average, document["period"]["days"]))
                if average
                else None,
                net_apy=str(annualized_yield(net, average, document["period"]["days"]))
                if average
                else None,
                yield_status="available" if average else "no_positive_exposure",
                issuer_incentive_program=program,
                issuer_incentive_accrued_usd=str(earned),
                issuer_cash_removed_usd=str(removed),
                investment_revenue_usd=str(investment),
                issuer_incentive_basis="ACT/365, UTC EOD eligible exposure; no cash rescaling",
            )
    document["issuer_incentive_reconciliation"] = reconciliation
    document["revenue_basis"] = (
        "Investment revenue plus accrued RLUSD/PYUSD issuer incentives; issuer cash excluded"
    )
