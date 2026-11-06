from copy import deepcopy
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from settle.api.app import create_app
from settle.revenue.financial import SPARK_HISTORY_POLICY, decode_selection, statements
from settle.revenue.financial_spark_reconciled import ASSET_FX, LIABILITY_FX, PSM, evidence
from tests.unit.test_financial_amms import source


def lookup(report):
    return {r["id"]: r for r in report["lines"]}


def amount(cell):
    return Decimal(cell["value"] if cell["value"] is not None else cell["known_value"])


def test_historical_PSM_credits_are_extra_income_once_and_old_selection_replays():
    r = statements("spark", 2026, date(2026, 10, 31), [], spark_statement=True)
    rows = lookup(r)
    credit = rows[PSM]["values"]["2026-04"]
    assert credit["value"] == "1113906.99"
    assert r["source_manifest"][credit["source_ref"]]["kind"] == "published_monthly_summary"
    assert sum(c["id"] == PSM for c in rows["statement.interest"]["components"]) == 1
    assert sum(c["id"] == PSM for c in rows["investment.susds"]["components"]) == 1
    assert rows[PSM]["values"]["2026-10"]["value"] is None
    assert rows[PSM]["values"]["2026-12"]["status"] == "future"
    # A monthly MSC fallback is not certified as the full Savings-funded return.
    assert rows["investment.susds.ethereum"]["values"]["2026-04"]["status"] != "available"
    assert (
        statements(
            "spark",
            2026,
            date(2026, 10, 31),
            [],
            selection=decode_selection(r["selection"], "spark", 2026),
        )
        == r
    )
    old_selection = decode_selection(r["selection"], "spark", 2026)
    old_selection["policy_version"] = SPARK_HISTORY_POLICY
    old = statements("spark", 2026, date(2026, 10, 31), [], selection=old_selection)
    assert PSM not in lookup(old)
    assert (
        statements(
            "spark",
            2026,
            date(2026, 10, 31),
            [],
            selection=decode_selection(old["selection"], "spark", 2026),
        )
        == old
    )


def test_recent_PSM_uses_matching_retained_raw_result_never_default_zero_or_provider():
    raw = source()
    raw["spark_psm3_income"] = "123.456"
    raw["spark_psm3_source_hash"] = raw["document"]["source"]["result_hash"]
    before = deepcopy(raw)
    doc = statements("spark", 2026, date(2026, 10, 27), [raw], spark_statement=True)
    cell = lookup(doc)[PSM]["values"]["2026-10"]
    assert (
        cell["known_value"] == "123.456"
        and cell["status"] == "partial"
        and cell["source_ref"] == raw["revision_id"]
    )
    assert raw == before
    wrong = deepcopy(raw)
    wrong["spark_psm3_source_hash"] = "bad"
    assert (
        lookup(statements("spark", 2026, date(2026, 10, 27), [wrong], spark_statement=True))[PSM][
            "values"
        ]["2026-10"]["value"]
        is None
    )
    absent = deepcopy(raw)
    absent.pop("spark_psm3_income")
    assert (
        lookup(statements("spark", 2026, date(2026, 10, 27), [absent], spark_statement=True))[PSM][
            "values"
        ]["2026-10"]["value"]
        is None
    )


def test_reviewed_anchor_cash_excludes_ALM_funding_and_discloses_unknown_April_payments():
    r = statements("spark", 2026, date(2026, 4, 30), [], spark_statement=True)
    rows = lookup(r)
    assert rows["investment.anchorage"]["values"]["2026-03"]["value"] == "805479"
    assert rows["investment.anchorage"]["values"]["2026-04"]["value"] == "0"
    assert rows["investment.anchorage"]["values"]["2026-03"]["recognition"] == "receipt"
    fact = next(x for x in r["spark_reviewed_cash_evidence"] if x["month"] == "2026-03")
    assert any(
        e["amount"] == "133500" and e["from"] == evidence()["alm"]
        for e in fact["anchorage"]["events"]
    )
    april = next(x for x in r["spark_msc_reconciliation"] if x["month"] == "2026-04")
    assert april["status"] == "review_required"
    anchor = next(x for x in april["venue_differences"] if x["id"] == "anchorage_cash")
    assert anchor["review_status"] == "review_required"
    # Nothing treats escrow payments as negative interest or automatically books an expense.
    assert april["operating_expenses_included"] is False
    assert "unconfirmed" in r["limitations"][-2]


def test_FX_breakdown_keeps_asset_gains_and_moves_depositor_FX_to_funding():
    raw = source()
    saved = {
        "revision_id": "a" * 64,
        "publication_order": 1,
        "cutoff": "2026-10-27",
        "source": {
            "kind": "spark_onchain_components",
            "revision_id": "a" * 64,
            "result_hash": "b" * 64,
            "cutoff": "2026-10-27",
            "currency": "USD",
        },
        "document": {
            "schema_version": "spark-onchain.v1",
            "period_start": "2026-10-01",
            "cutoff": "2026-10-27",
            "gains": {
                "sparklend_nonstable": {
                    "status": "available",
                    "value": "-0.000000000000000000123",
                    "asset_price_gain_usd": "-1000000.000000000000000000123",
                    "depositor_offset_usd": "-1000000",
                    "quality": "supported",
                    "recognition": "accrual",
                }
            },
            "savings_interest": {
                "S58": {
                    "status": "available",
                    "value": "7",
                    "recognition": "accrual",
                    "quality": "supported",
                }
            },
        },
    }
    r = statements(
        "spark", 2026, date(2026, 10, 27), [raw], spark_statement=True, spark_publications=[saved]
    )
    rows = lookup(r)
    assert amount(rows[ASSET_FX]["values"]["2026-10"]) == Decimal("-1000000.000000000000000000123")
    assert amount(rows[LIABILITY_FX]["values"]["2026-10"]) == 1000000
    assert amount(rows["gains.sparklend_nonstable"]["values"]["2026-10"]) == Decimal(
        "-1000000.000000000000000000123"
    )
    assert amount(rows["funding.savings"]["values"]["2026-10"]) == Decimal("-999993")
    assert ASSET_FX not in [c["id"] for c in rows["statement.gains"]["components"]]
    assert LIABILITY_FX not in [c["id"] for c in rows["statement.funding"]["components"]]
    assert amount(rows["funding.savings.eth"]["values"]["2026-10"]) == 7
    for key in [ASSET_FX, "funding.savings.revaluation"]:
        entry = next(x for x in r["statement_rows"] if x["metric_id"] == key)
        assert entry["parent"] == ("gains.sparklend_nonstable" if key == ASSET_FX else "funding.savings")
        assert entry["depth"] == 3


def test_bridge_does_not_claim_arithmetic_difference_proves_reconciliation():
    r = statements("spark", 2026, date(2026, 10, 31), [], spark_statement=True)
    bridge = r["spark_msc_reconciliation"]
    assert all(b["status"] == "review_required" for b in bridge[:9])
    assert bridge[9]["status"] == "unavailable"
    assert all(b["principal_included"] is False for b in bridge)
    jan = bridge[0]
    assert jan["reviewed_cash_evidence"]["pyusd"]["onchain_received"] == "0"
    assert jan["reviewed_cash_evidence"]["pyusd"]["MSC_S28_revenue"] == "0.00"
    assert r["spark_fee_true_up_control"]["added_to_reporting_income"] is False
    # Other primes keep their existing policy/perimeter.
    grove = statements(
        "grove", 2026, date(2026, 10, 31), [], monthly=True, rollups=True, archive=True
    )
    assert "spark_msc_reconciliation" not in grove


def test_read_only_bridge_endpoint_and_old_selection_are_explicit(monkeypatch):
    from settle.api import revenue
    from settle.revenue.financial import selection_token

    monkeypatch.setattr(revenue, "today", lambda: date(2026, 11, 5))
    monkeypatch.setattr(
        "requests.Session.send",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("No provider calls")),
    )

    class Reader:
        def financial_year(self, *a, **k):
            return []

        def spark_component_year(self, *a, **k):
            return []

    client = TestClient(create_app(reader=Reader()))
    response = client.get(
        "/v3/reporting/spark/msc-reconciliation", params={"year": 2026, "month": "2026-04"}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["months"]) == 1 and data["months"][0]["month"] == "2026-04"
    assert data["reserve_fee_true_up_control"]["added_to_reporting_income"] is False
    assert (
        client.get(
            "/v3/reporting/spark/msc-reconciliation", params={"year": 2026, "month": "2025-04"}
        ).status_code
        == 422
    )
    old = decode_selection(data["selection"], "spark", 2026)
    old["policy_version"] = SPARK_HISTORY_POLICY
    old.pop("expense_input_revision", None)
    assert (
        client.get(
            "/v3/reporting/spark/msc-reconciliation",
            params={"year": 2026, "revision": selection_token(old)},
        ).status_code
        == 404
    )
    latest = client.get(
        "/v3/reporting/spark/msc-reconciliation", params={"year": 2026, "month": "2026-10"}
    ).json()["months"][0]
    assert (
        latest["net_interest_difference"]["value"] is None
        and latest["net_interest_difference"]["known_value"] is None
    )


def test_reviewed_scope_does_not_leak_2026_cash_into_other_years():
    r = statements("spark", 2025, date(2025, 12, 31), [], spark_statement=True)
    assert r["policy_version"] == SPARK_HISTORY_POLICY
    assert "spark_msc_reconciliation" not in r


def test_historical_cutoff_does_not_compare_future_fee_trueup_or_April_cash():
    r = statements("spark", 2026, date(2026, 3, 31), [], spark_statement=True)
    assert r["spark_fee_true_up_control"]["amount"] is None
    assert r["spark_fee_true_up_control"]["difference"]["known_value"] is None
    assert not any(f["month"] == "2026-04" for f in r["spark_reviewed_cash_evidence"])
