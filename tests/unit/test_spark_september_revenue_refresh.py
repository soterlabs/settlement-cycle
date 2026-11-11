import copy
import json
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("scripts").resolve()))
from refresh_spark_september_allocation_revenue import FOLDER, TARGETS
from refresh_spark_september_allocation_revenue import refresh as refresh_impl


def refresh(b, e):
    return refresh_impl(b, e, json.loads((FOLDER / "incentive-accruals.json").read_text()))


def inputs():
    return [json.loads((FOLDER / n).read_text()) for n in ("baseline.json", "evidence.json")]


def test_refresh_changes_only_target_revenue_and_preserves_costs():
    b, e = inputs()
    original = copy.deepcopy(b)
    result = refresh(b, e)
    assert b == original
    for old, new in zip(b["allocations"], result["allocations"], strict=True):
        assert old["borrowing_cost_usd"] == new["borrowing_cost_usd"]
        if old["venue_id"] not in TARGETS:
            assert old == new
        else:
            assert D(new["net_pnl_usd"]) == D(new["prime_retained_revenue_usd"]) - D(
                old["borrowing_cost_usd"]
            )
    rows = {r["venue_id"]: r for r in result["allocations"]}
    assert abs(
        D(rows["S64"]["gross_revenue_usd"]) - D(rows["S64"]["issuer_incentive_accrued_usd"])
    ) < D("1e-18")
    assert D(rows["S70"]["investment_revenue_usd"]) == D("2400.001564107802276268")
    assert D(rows["S72"]["investment_revenue_usd"]) == D("3674.1568632270234742260")
    assert rows["S67"]["gross_revenue_usd"] is None
    assert result["reconciliation"] == b["reconciliation"]


def test_rejects_edited_baseline():
    b, e = inputs()
    b["allocations"][0]["borrowing_cost_usd"] = "0"
    with pytest.raises(ValueError, match="Baseline"):
        refresh(b, e)


@pytest.mark.parametrize(
    "field,value", [("block_time", 1790812800), ("topic1", "0x00"), ("address", "0x00")]
)
def test_receipts_must_match_period_payer_and_token(field, value):
    b, e = inputs()
    e["receipts"][0][field] = value
    with pytest.raises(ValueError, match="receipt"):
        refresh(b, e)


def test_no_double_counting_duplicate_receipts():
    b, e = inputs()
    e["receipts"].append(e["receipts"][0])
    with pytest.raises(ValueError, match="duplicate"):
        refresh(b, e)


def test_refreshed_document_cannot_be_applied_again():
    from settle.revenue.verification import digest

    b, e = inputs()
    new = refresh(b, e)
    new["revision_id"] = digest(new)
    new["published_at"] = b["published_at"]
    with pytest.raises(ValueError, match="Baseline"):
        refresh(new, e)


def test_only_revenue_and_yield_fields_change_for_target_rows():
    b, e = inputs()
    result = refresh(b, e)
    allowed = {
        "gross_revenue_usd",
        "prime_retained_revenue_usd",
        "net_pnl_usd",
        "gross_monthly_return",
        "net_monthly_return",
        "gross_apy",
        "net_apy",
        "yield_status",
        "average_yield_exposure_usd",
        "yield_denominator",
        "spread_reimbursement_excluded_from_net_usd",
        "issuer_incentive_program",
        "issuer_incentive_accrued_usd",
        "issuer_cash_removed_usd",
        "investment_revenue_usd",
        "issuer_incentive_basis",
    }
    for old, new in zip(b["allocations"], result["allocations"], strict=True):
        assert {k: v for k, v in old.items() if k not in allowed} == {
            k: v for k, v in new.items() if k not in allowed
        }


@pytest.mark.parametrize("field", ["actual_revenue", "tw_avg_value"])
def test_retained_income_and_exposure_must_match_dated_capital(field):
    b, e = inputs()
    e["venues"]["S72"][field] = str(D(e["venues"]["S72"][field]) + 1)
    with pytest.raises(ValueError, match="differs"):
        refresh(b, e)


def test_cash_is_removed_and_accrual_is_not_rescaled_to_receipts():
    from settle.revenue.allocation_incentives import component_accruals

    i = json.loads((FOLDER / "incentive-accruals.json").read_text())
    allocation, recon = component_accruals(i)
    assert D(recon["rlusd"]["accrued_usd"]) != D(recon["rlusd"]["cash_received_usd"])
    changed = copy.deepcopy(i)
    changed["programs"]["rlusd"]["period_receipts_usd"] = "0"
    assert component_accruals(changed)[0] == allocation
    b, e = inputs()
    rows = {r["venue_id"]: r for r in refresh(b, e)["allocations"]}
    assert D(rows["S28"]["investment_revenue_usd"]) == 0
    assert D(rows["S28"]["issuer_cash_removed_usd"]) == D("1809960")
    assert rows["S67"]["gross_revenue_usd"] is None
    assert rows["S68"]["gross_revenue_usd"] is None
    assert recon["rlusd"]["opening_receivable_usd"] is None
    assert recon["rlusd"]["closing_receivable_usd"] is None


@pytest.mark.parametrize(
    "fault", ["missing_day", "duplicate_component", "wrong_rate", "missing_top_up_apr"]
)
def test_incomplete_or_inconsistent_incentive_inputs_fail(fault):
    from settle.revenue.allocation_incentives import component_accruals

    i = json.loads((FOLDER / "incentive-accruals.json").read_text())
    days = i["programs"]["rlusd"]["daily"]
    if fault == "missing_day":
        days.pop()
    elif fault == "duplicate_component":
        days[0]["components"].append(days[0]["components"][0])
    elif fault == "wrong_rate":
        days[0]["components"][0]["apr"] = "0.04"
    else:
        days[0]["components"][-1]["net_vault_apr"] = None
    with pytest.raises(ValueError):
        component_accruals(i)


def test_whole_document_revenue_delta_excludes_duplicate_cash_and_accrual_rows():
    b, e = inputs()
    result = refresh(b, e)
    old = sum(
        (D(r["gross_revenue_usd"]) for r in b["allocations"] if r["gross_revenue_usd"] is not None),
        D(0),
    )
    new = sum(
        (
            D(r["gross_revenue_usd"])
            for r in result["allocations"]
            if r["gross_revenue_usd"] is not None
        ),
        D(0),
    )
    incentives = result["issuer_incentive_reconciliation"]
    expected = (
        D(incentives["rlusd"]["accrued_usd"])
        + D(incentives["pyusd"]["accrued_usd"])
        - D("1809960")
        + D("2400.001564107802276268")
        + D("3674.1568632270234742260")
    )
    assert abs(new - old - expected) < D("1e-18")
    # No changes to MSC costs, financing reconciliation, source income-only rows,
    # or unrelated investments are allowed to absorb the cash/accrual mismatch.
    assert b["reconciliation"] == result["reconciliation"]


def test_reference_series_must_be_fed_lower_bound():
    from settle.revenue.allocation_incentives import component_accruals

    i = json.loads((FOLDER / "incentive-accruals.json").read_text())
    i["reference_series"] = "SOFR"
    with pytest.raises(ValueError, match="reference-rate"):
        component_accruals(i)


def test_incentives_use_the_same_september_block_boundaries():
    b, e = inputs()
    i = json.loads((FOLDER / "incentive-accruals.json").read_text())
    i["closing_pins"]["ethereum"] += 1
    with pytest.raises(ValueError, match="block pins"):
        refresh_impl(b, e, i)
