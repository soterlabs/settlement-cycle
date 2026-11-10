from copy import deepcopy
from datetime import date

import pytest
from fastapi.testclient import TestClient

from settle.api.app import create_app
from settle.revenue.balance_sheet import annual, decode
from settle.revenue.cash_flow.config import layout, load
from settle.revenue.cash_flow.model import metric, subtotals
from settle.revenue.verification import digest


def example(cutoff="2026-01-31", index=1):
    metrics = {r["id"]: metric("0") for r in layout()}
    metrics["net-income"] = metric("50")
    metrics["measured-cash"] = metric("100")
    subtotals(metrics)
    metrics["earned-not-received"]["approved_msc_component"] = metric("0")
    metrics["earned-not-received"]["steakhouse_income_component"] = metric("0")
    doc = dict(
        schema_version="cash-flow-month.v1",
        policy_version=load()["policy"],
        prime="spark",
        cutoff=cutoff,
        period_start=cutoff[:7] + "-01",
        configuration_revision=load()["revision"],
        metrics=metrics,
        coverage={"missing_chains": [], "unclassified_events": 0},
        dependencies={},
    )
    cash = dict(
        revision_id=str(index) * 64,
        publication_order=index,
        cutoff=cutoff,
        prime="spark",
        document=doc,
        policy_version=doc["policy_version"],
        document_hash=digest(doc),
        evidence_hash=digest([]),
        code_version="test",
        configuration_revision=load()["revision"],
        computed_at="2026-11-08T00:00:00Z",
    )

    def venue(id, value):
        return dict(
            venue_id=id,
            label=id,
            status="available",
            enabled=True,
            method="existing",
            position_value_usd=value,
        )

    source = dict(
        revision_id="a" * 64,
        publication_order=index,
        cutoff=cutoff,
        result_hash="b" * 64,
        venues=[
            venue("S1", "1000"),
            venue("S26", "100"),
            venue("S56", "250"),
            venue("S67", "-20"),
            venue("S68", "30"),
            venue("S62", "50"),
        ],
    )
    components = dict(
        revision_id="c" * 64,
        publication_order=index,
        cutoff=cutoff,
        document_hash="d" * 64,
        document={"steakhouse": {"status": "available", "closing_value": "200"}},
    )
    return {"venues": [source], "cash": [cash], "components": [components]}


def line(report, id, month="2026-01"):
    return next(r for r in report["lines"] if r["id"] == id)["values"][month]


def test_stocks_principal_savings_cash_overlap_and_prepaid_rewards():
    report = annual(2026, date(2026, 1, 31), example())
    assert line(report, "position.S1")["value"] == "1000"  # full claim, not monthly income
    assert line(report, "cash")["value"] == "100"
    assert not any(
        r["id"] == "position.S26" for r in report["lines"]
    )  # same cash is not another allocation
    assert not any(
        r["id"] == "position.S56" for r in report["lines"]
    )  # depositor claim is a liability
    assert line(report, "savings.S56")["value"] == "250"
    assert line(report, "reward_receivable.S67")["value"] == "0"
    assert line(report, "reward_advance.S67")["value"] == "20"
    assert line(report, "reward_receivable.S68")["value"] == "30"
    assert line(report, "position.S62")["status"] == "unavailable"  # no invented Sky split
    assert line(report, "sky_debt")["value"] is None
    assert line(report, "contributed_capital")["value"] is None
    assert line(report, "total_assets")["status"] == "partial"
    assert line(report, "total_equity")["status"] == "partial"
    assert report["total_label"] == "Latest"


def test_latest_is_not_sum_and_current_earnings_accumulate_without_opening_equity_plug():
    data = example()
    feb = example("2026-02-28", 2)
    for kind in data:
        for r in feb[kind]:
            r["revision_id"] = (
                "e" * 64
                if kind == "venues"
                else "f" * 64
                if kind == "components"
                else r["revision_id"]
            )
        data[kind] += feb[kind]
    report = annual(2026, date(2026, 2, 28), data)
    assert line(report, "current_earnings", "2026-02")["value"] == "100"
    cash = next(r for r in report["lines"] if r["id"] == "cash")
    assert cash["total"]["value"] == "100"  # not 200
    assert line(report, "cash", "2026-03")["status"] == "future"
    selected = decode(report["selection"], "spark", 2026)
    assert annual(2026, date(2026, 2, 28), data, selection=selected) == report
    data["venues"].pop()
    with pytest.raises(LookupError):
        annual(2026, date(2026, 2, 28), data, selection=selected)


def test_current_month_aligns_source_cutoffs_and_does_not_reuse_past_month_for_missing_eom():
    data = example("2026-01-30")
    report = annual(2026, date(2026, 2, 10), data)
    assert report["periods"][0]["actual_cutoff"] is None
    assert line(report, "cash")["status"] == "unavailable"
    data = example("2026-01-30")
    report = annual(2026, date(2026, 1, 31), data)
    assert report["periods"][0]["actual_cutoff"] is None  # historic month requires EOM
    data = example("2026-01-29")
    newer = deepcopy(data["cash"][0])
    newer["cutoff"] = "2026-01-30"
    newer["document"]["cutoff"] = "2026-01-30"
    newer["revision_id"] = "9" * 64
    data["cash"].append(newer)
    report = annual(2026, date(2026, 1, 30), data)
    assert report["periods"][0]["actual_cutoff"] == "2026-01-29"
    assert report["periods"][0]["stale"] is True


def test_unavailable_is_not_zero_and_unknown_selection_rejected():
    report = annual(2026, date(2026, 1, 31), {"venues": [], "cash": [], "components": []})
    assert line(report, "total_assets")["status"] == "unavailable"
    assert line(report, "savings.S56")["value"] is None
    with pytest.raises(ValueError):
        decode("oops", "spark", 2026)
    with pytest.raises(ValueError):
        annual(2025, date(2025, 1, 31), example())


def test_api_reads_stored_inputs_only_and_rejects_other_primes(monkeypatch):
    monkeypatch.setattr("settle.api.revenue.today", lambda: date(2026, 11, 10))

    class Reader:
        def balance_sheet_inputs(self, year, as_of, selection):
            return example()

    with TestClient(create_app(reader=Reader())) as client:
        r = client.get("/v3/reporting/balance-sheet?prime=spark&year=2026&cutoff=2026-01-31")
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["prime"] == "spark"
        assert client.get("/v3/reporting/balance-sheet?prime=grove&year=2026").status_code == 404
        assert (
            client.get(
                "/v3/reporting/balance-sheet?prime=spark&year=2026&cutoff=2026-11-10"
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/v3/reporting/balance-sheet",
                params=dict(
                    prime="spark", year=2026, revision=doc["selection"], cutoff="2026-01-31"
                ),
            ).status_code
            == 422
        )
        replay = client.get(
            "/v3/reporting/balance-sheet",
            params=dict(prime="spark", year=2026, revision=doc["selection"]),
        )
        assert replay.status_code == 200 and replay.json() == doc
