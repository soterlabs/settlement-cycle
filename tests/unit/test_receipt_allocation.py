from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext

import pytest

from settle.revenue.financial import metric, statements
from settle.revenue.receipt_allocation import STREAMS, equal_days, project


def report(month="2026-10", cutoff="2026-10-31", **cells):
    d = statements("spark", 2026, date.fromisoformat(cutoff), [], spark_statement=True)
    d["source_lag_periods"] = []
    d["source_manifest"]["receipt-source"] = {"cutoff": cutoff, "kind": "spark_onchain_components"}
    for row in d["lines"]:
        if row["id"] not in STREAMS:
            continue
        cell = metric("0", basis="verified_receipts")
        cell.update(recognition="receipt", quality="supported", source_ref="receipt-source")
        row["values"][month] = cells.get(row["id"], cell)
    return d


def cash(amount, *, status="available", end="2026-10-31"):
    cell = metric(amount if status == "available" else None, basis="verified_receipts")
    cell.update(recognition="receipt", quality="supported", period_end=end)
    if status == "partial":
        cell.update(status=status, known_value=amount)
    return cell


def total(rows):
    with localcontext() as ctx:
        ctx.prec = 150
        return sum((Decimal(r["value"] if r["value"] is not None else r["known_value"]) for r in rows), Decimal(0))


@pytest.mark.parametrize("amount", ["100.01", "-0.01", "0", "1E-30", "1E+100", "9007199254740993.123456789012345678901234567890123456789"])
def test_equal_calendar_days_preserve_exact_signed_amount_and_subcent_precision(amount):
    rows = equal_days(Decimal(amount), 31)
    with localcontext() as ctx:
        ctx.prec = 150
        assert sum(map(Decimal, rows)) == Decimal(amount)
        assert len(set(rows[:-1])) == 1
    if Decimal(amount) != 0:
        assert all(Decimal(v) != 0 for v in rows)


def test_receipts_only_no_subtotals_no_native_yield_and_no_statement_or_principal_changes():
    d = report(**{"investment.anchorage": cash("310")})
    before = deepcopy(d)
    result = project(d, "2026-10")
    rows = {r["metric_id"]: r for r in result["streams"]}
    assert set(rows) == set(STREAMS)
    assert "fees.sparklend" not in rows and "statement.rewards" not in rows
    assert "investment.venue.S1" not in rows and "rewards.issuer.rlusd" not in rows
    assert "rewards.issuer.pyusd" not in rows and "fees.curator" not in rows
    original = next(r for r in before["lines"] if r["id"] == "investment.anchorage")["values"]["2026-10"]
    assert rows["investment.anchorage"]["monthly_receipts"] == original
    assert all(Decimal(r["value"]) == 10 for r in rows["investment.anchorage"]["daily"])
    assert d == before and result["source_report_hash"] == before["report_hash"]
    assert result["true_accrual"] is False and result["principal_included"] is False
    assert all(c["quality"] == "estimate" and c["recognition"] == "receipt" for c in rows["investment.anchorage"]["daily"])


def test_partial_mtd_allocates_only_observed_days_and_does_not_invent_uncovered_or_future_income():
    d = report("2026-11", "2026-11-04", **{"investment.anchorage": cash("30", status="partial", end="2026-11-03")})
    row = project(d, "2026-11", "investment.anchorage")["streams"][0]
    assert row["allocated_days"] == 3
    assert [r["date"] for r in row["daily"]] == [f"2026-11-0{i}" for i in range(1, 5)]
    assert all(r["value"] is None and r["known_value"] == "10.000000000000000000" for r in row["daily"][:3])
    assert row["daily"][3]["value"] is None and row["daily"][3]["known_value"] is None
    assert total(row["daily"][:3]) == 30


def test_missing_cash_is_not_zero_and_missing_coverage_is_not_assumed():
    d = report(**{"investment.anchorage": metric(reason="missing_loan_source")})
    row = project(d, "2026-10", "investment.anchorage")["streams"][0]
    assert row["allocated_days"] == 0 and len(row["daily"]) == 31
    assert all(r["value"] is None and r["known_value"] is None for r in row["daily"])
    d = report(**{"investment.anchorage": metric("0", basis="unknown")})
    row = project(d, "2026-10", "investment.anchorage")["streams"][0]
    assert row["allocated_days"] == 0


def test_original_payment_day_is_preserved_separately_and_principal_is_never_spread():
    d = report(**{"investment.anchorage": cash("310")})
    pub = {"revision_id": "receipt-source", "cutoff": "2026-10-31", "document": {"statement_allocations": {"anchorage.receipts": {"status": "available", "daily_receipts": [{"date": "2026-10-31", "day_net_received_usd": "1000310", "principal_excluded_usd": "1000000", "income_usd": "310"}]}}}}
    row = project(d, "2026-10", "investment.anchorage", components=[pub])["streams"][0]
    assert row["actual_receipts"]["rows"] == [{"date": "2026-10-31", "income_usd": "310"}]
    assert total(row["daily"]) == 310
    pub["document"]["statement_allocations"]["anchorage.receipts"]["daily_receipts"][0]["income_usd"] = "1000310"
    assert project(d, "2026-10", "investment.anchorage", components=[pub])["streams"][0]["actual_receipts"]["rows"] is None


def test_dormant_buidl_zero_can_be_allocated_but_generic_nonzero_return_is_never_cash():
    zero = metric("0", basis="retained_RWA_revenue_excluding_capital_flows")
    zero.update(recognition="investment_return", period_end="2026-10-31")
    d = report(**{"investment.rwa.buidl": zero})
    row = project(d, "2026-10", "investment.rwa.buidl")["streams"][0]
    assert row["allocated_days"] == 31 and total(row["daily"]) == 0
    zero["value"] = "10"
    d = report(**{"investment.rwa.buidl": zero})
    row = project(d, "2026-10", "investment.rwa.buidl")["streams"][0]
    assert row["allocated_days"] == 0 and row["monthly_receipts"]["value"] is None
    assert row["source_monthly_metric"]["value"] == "10"


def test_unknown_stream_and_future_selected_period_are_rejected():
    d = report()
    with pytest.raises(KeyError):
        project(d, "2026-10", "fees.curator")
    with pytest.raises(ValueError, match="does not cover"):
        project(d, "2026-11")


@pytest.mark.parametrize("month,end,days", [("2026-02", "2026-02-28", 28), ("2026-04", "2026-04-30", 30)])
def test_full_month_uses_actual_calendar_length(month, end, days):
    d = report(month, end, **{"investment.anchorage": cash(str(days), end=end)})
    row = project(d, month, "investment.anchorage")["streams"][0]
    assert row["allocated_days"] == days and len(row["daily"]) == days
    assert all(Decimal(c["value"]) == 1 for c in row["daily"])


def test_claimed_coverage_beyond_source_cutoff_is_not_allocated():
    c = cash("30", end="2026-11-04")
    c["source_ref"] = "receipt-source"
    d = report("2026-11", "2026-11-04", **{"investment.anchorage": c})
    d["source_manifest"]["receipt-source"]["cutoff"] = "2026-11-03"
    row = project(d, "2026-11", "investment.anchorage")["streams"][0]
    assert row["allocated_days"] == 0
    assert all(c["value"] is None for c in row["daily"])
