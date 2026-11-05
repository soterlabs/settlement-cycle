"""Approved MSC entitlements and execution-period true-ups, never gross venue revenue."""

import json
from datetime import date
from decimal import Decimal, localcontext
from importlib.resources import files

from ..verification import digest
from .model import metric


def entitlement(cutoff, records=None):
    records = (
        records
        if records is not None
        else json.loads(files(__package__).joinpath("settlements.json").read_text())["records"]
    )
    month = str(cutoff)[:7]
    base = []
    adjustments = []
    refs = []
    with localcontext() as ctx:
        ctx.prec = 100
        for r in records:
            demand, share, passed, dc, sc = map(
                Decimal,
                [
                    r["demand_usds"],
                    r["spark_share_usds"],
                    r["pass_through_usds"],
                    r["demand_correction_usds"],
                    r["supply_correction_usds"],
                ],
            )
            sent = Decimal(r["expected_send_usds"])
            rounding = sent - demand - share - passed - sc
            if (
                not all(x.is_finite() for x in [demand, share, passed, dc, sc, sent])
                or abs(rounding) > 1
            ):
                raise ValueError("Executed settlement components do not reconcile")
            if r["earned_month"] == month:
                base.append(demand - dc + share + passed)
                refs.append(r["source_url"])
            if (
                r["execution_date"][:7] == month
                and r["earned_month"].startswith("2026-")
                and date.fromisoformat(r["execution_date"]) <= cutoff
            ):
                adjustments.append(dc + sc + rounding)
                refs.append(r["source_url"])
        if len(base) > 1:
            raise ValueError("Ambiguous monthly MSC entitlement")
        value = sum(base + adjustments, Decimal(0)) if base or any(adjustments) else None
        result = metric(
            value,
            complete=bool(base),
            recognition="accrual",
            refs=sorted(set(refs)),
            reason="Approved net MSC entitlement plus execution-period prior-month true-ups; retrospectively sourced"
            if base
            else "Regular monthly entitlement is not published; only known executed true-ups are included",
        )
        result["basis"] = "approved_net_msc_entitlement_and_executed_corrections"
        result["source_revision"] = digest(records)
        return result
