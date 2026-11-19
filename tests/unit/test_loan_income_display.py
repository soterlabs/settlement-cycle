import json
from copy import deepcopy
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import pytest

from settle.load.cof_attribution import compute_sheet_rows
from settle.load.loan_income_display import display_rows, receipt_amount
from settle.load.summary import render_summary


def october():
    return json.loads(
        (
            Path(__file__).parents[2]
            / "reconciliation/2026-10/retained-close/spark/provenance.json"
        ).read_text()
    )


def test_october_attribution_preserves_cash_and_totals():
    p = october()
    before = deepcopy(p)
    projected = display_rows(p, p["venue_breakdown"])
    rows = {r["venue_id"]: r for r in projected}
    assert D(rows["S23"]["revenue"]) == D("967807")
    assert D(rows["S26"]["revenue"]) == 0
    with localcontext() as ctx:
        ctx.prec = 100
        for key in ("actual_revenue", "revenue", "value_som", "value_eom", "period_inflow"):
            assert sum(D(r[key]) for r in projected) == sum(D(r[key]) for r in p["venue_breakdown"])
    assert p == before
    summary = render_summary(p)
    assert "Anchorage loan — interest receipts (balances: escrow only)" in summary
    assert "USDC raw (ALM idle)" in summary
    assert "not outstanding loan principal" in summary


def test_other_usdc_income_and_balances_are_not_assigned_to_anchorage():
    p = october()
    raw = next(r for r in p["venue_breakdown"] if r["venue_id"] == "S26")
    cash = next(r for r in p["venue_views"]["settlement"] if r["venue_id"] == "S26")
    raw.update(revenue="967907", actual_revenue="967907", value_eom="10000000")
    cash.update(income_usd="100", income_before_attribution_usd="967907")
    rows = {r["venue_id"]: r for r in display_rows(p, p["venue_breakdown"])}
    assert D(rows["S26"]["revenue"]) == 100
    assert D(rows["S26"]["value_eom"]) == 10000000
    assert D(rows["S23"]["revenue"]) == 967807


def test_workbook_costs_and_aggregate_profit_do_not_change():
    p = october()
    sheet, _ = compute_sheet_rows(p, "spark")
    after = display_rows(p, sheet, workbook=True)
    with localcontext() as ctx:
        ctx.prec = 100
        for key in ("actual_rev", "revenue", "profit_to_grove", "profit_to_sky", "cof_alloc"):
            assert sum(r[key] for r in sheet) == sum(r[key] for r in after)
    for old, new in zip(sheet, after, strict=True):
        assert old["cof_alloc"] == new["cof_alloc"]
        assert old["profit_to_sky"] == new["profit_to_sky"]


def test_no_retained_attribution_means_no_guess():
    p = october()
    p["venue_views"] = None
    assert display_rows(p, p["venue_breakdown"]) == p["venue_breakdown"]
    p = october()
    p["prime_id"] = "grove"
    assert display_rows(p, p["venue_breakdown"]) == p["venue_breakdown"]


def test_corrupt_attribution_cannot_be_rendered_as_income():
    p = october()
    next(r for r in p["venue_views"]["settlement"] if r["venue_id"] == "S23")[
        "cash_received_usd"
    ] = "999999"
    with pytest.raises(ValueError, match="reconcile"):
        receipt_amount(p)
