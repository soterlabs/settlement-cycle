"""Freeze independent API views without changing any MSC calculation.

Only normalized, already-classified cash flows enter this module. No I/O.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from ..domain.venue_views import amount, validate_loan_terms

ZERO = Decimal(0)
METRICS = ("opening_position_value_usd", "position_value_usd", "income_usd",
           "cash_received_usd", "principal_usd", "opening_unpaid_income_usd", "unpaid_income_usd")


def _at(schedule, day, key):
    return next((amount(row[key]) for row in reversed(schedule)
                 if date.fromisoformat(row["date"]) <= day), ZERO)


def loan_accrual(terms, period, receipts):
    """Simple daily interest on EOD principal; opening balance is before opening_date.

    Principal schedules are absolute balances, rates are net contractual APRs.
    Repayments change principal through the schedule, never through cash income.
    """
    validate_loan_terms(terms)
    opening_date = date.fromisoformat(terms["opening_date"])
    if not opening_date <= period.start <= period.end <= date.fromisoformat(terms["confirmed_through"]):
        raise ValueError("loan terms do not cover the requested period")
    denominator = Decimal(365 if terms["day_count"] == "act/365" else 360)
    unpaid = amount(terms["opening_unpaid_interest_usd"])
    period_income = period_cash = ZERO
    daily = []
    opening_unpaid = unpaid

    def principal_at(day):
        return sum((_at(t["principal_schedule"], day, "principal_usd")
                    for t in terms["tranches"]), ZERO)

    day = opening_date
    while day <= period.end:
        if day == period.start:
            opening_unpaid = unpaid
        income = sum((_at(t["principal_schedule"], day, "principal_usd")
                      * _at(t["apr_schedule"], day, "apr") / denominator
                      for t in terms["tranches"]), ZERO)
        cash = sum((amount(r["amount_usd"]) for r in receipts if r["date"] == str(day)), ZERO)
        unpaid += income - cash
        if day >= period.start:
            period_income += income
            period_cash += cash
            daily.append({"date": str(day), "principal_usd": str(principal_at(day)),
                          "income_usd": str(income), "cash_received_usd": str(cash),
                          "unpaid_income_usd": str(unpaid)})
        day += timedelta(days=1)
    principal = principal_at(period.end)
    return {
        "opening_position_value_usd": str(principal_at(period.start - timedelta(days=1)) + opening_unpaid),
        "position_value_usd": str(principal + unpaid), "principal_usd": str(principal),
        "income_usd": str(period_income), "cash_received_usd": str(period_cash),
        "opening_unpaid_income_usd": str(opening_unpaid), "unpaid_income_usd": str(unpaid),
        "daily": daily, "terms_source": terms["source"], "day_count": terms["day_count"],
        "terms_confirmed_through": terms["confirmed_through"],
    }


def build_venue_views(prime, pnl, receipts):
    period = pnl.period
    existing = {r.venue_id: r for r in pnl.venue_breakdown + pnl.reporting_only_breakdown}
    outputs = {"schema_version": "2.0", "settlement": [], "reporting": []}
    attributed = {}
    deductions = {}
    for venue in prime.venues:
        cfg = venue.settlement_view
        if cfg.method != "alm_receipts" or not venue.settles or venue.id not in existing:
            continue
        matches = [r for r in receipts if r["source_venue"] == cfg.source_venue
                   and r["payer"].lower() == cfg.payer.lower()]
        cash = sum((amount(r["amount_usd"]) for r in matches
                    if str(period.start) <= r["date"] <= str(period.end)), ZERO)
        attributed[venue.id] = (cash, matches)
        deductions[cfg.source_venue] = deductions.get(cfg.source_venue, ZERO) + cash

    for venue in prime.venues:
        saved = existing.get(venue.id)
        for kind, cfg in (("settlement", venue.settlement_view), ("reporting", venue.reporting_view)):
            enabled = venue.settles if kind == "settlement" else cfg.enabled
            row = {"venue_id": venue.id, "label": venue.label, "chain": venue.chain.value,
                   "enabled": enabled, "method": cfg.method,
                   "status": "available", "reason": None, **dict.fromkeys(METRICS)}
            outputs[kind].append(row)
            if not enabled:
                row.update(status="disabled", reason="disabled in configuration")
                continue
            if venue.start_date and venue.start_date > period.end:
                row.update(status="not_started", reason="venue starts after the cutoff")
                continue
            if saved is None:
                row.update(status="unavailable", reason="no venue calculation for this period")
                continue
            row.update(opening_position_value_usd=str(saved.value_som),
                       position_value_usd=str(saved.value_eom),
                       income_usd=str(saved.revenue - deductions.get(venue.id, ZERO)))
            if venue.id in deductions:
                row["income_attributed_to"] = [v.id for v in prime.venues
                                               if v.id in attributed and v.settlement_view.source_venue == venue.id]
                row["income_before_attribution_usd"] = str(saved.revenue)
            incentive = saved.incentive_accrual
            if incentive:
                row.update(cash_received_usd=incentive["period_receipts"],
                           opening_unpaid_income_usd=str(saved.value_som),
                           unpaid_income_usd=str(saved.value_eom))
            if venue.id in attributed:
                cash = attributed[venue.id][0]
                row.update(income_usd=str(saved.revenue + cash), cash_received_usd=str(cash),
                           receipt_attribution={"source_venue": venue.settlement_view.source_venue,
                                                "payer": venue.settlement_view.payer.lower()})
            if cfg.method == "loan_accrual":
                # The escrow balance is not a loan valuation. Never substitute
                # it, or cash income, when contractual accrual is unavailable.
                cash = row["cash_received_usd"]
                row.update(dict.fromkeys(METRICS))
                row["cash_received_usd"] = cash
                if cfg.loan_terms is None:
                    row.update(status="unavailable", reason=cfg.pending_reason)
                elif (date.fromisoformat(cfg.loan_terms["opening_date"]) < prime.start_date
                      or cfg.loan_terms["opening_date"] > str(period.start)
                      or cfg.loan_terms["confirmed_through"] < str(period.end)):
                    row.update(status="unavailable", reason="loan terms or receipt history do not cover the period")
                else:
                    row.update(loan_accrual(cfg.loan_terms, period, attributed[venue.id][1]))
    return outputs
