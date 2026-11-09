from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from settle.api.app import create_app
from settle.revenue.cash_flow.build import expense_inputs
from settle.revenue.cash_flow.collector import DEPOSIT, TRANSFER, WITHDRAW
from settle.revenue.cash_flow.config import layout, load
from settle.revenue.cash_flow.liability import ZERO, split
from settle.revenue.cash_flow.model import metric, subtotals, total, validate
from settle.revenue.cash_flow.project import annual, decode
from settle.revenue.verification import digest

OWNER = "0x" + "1" * 40
OTHER = "0x" + "2" * 40
VAULT = "0x" + "3" * 40


def topic(a):
    return "0x" + a[2:].zfill(64)


def event(i, kind, first, second, *, src=OWNER, dst=OWNER, owner=OWNER):
    return {
        "chain": "ethereum",
        "address": VAULT,
        "block": i,
        "log_index": 0,
        "tx_hash": "0x" + str(i).zfill(64),
        "timestamp": 1767225600 + i,
        "topic0": kind,
        "topic1": topic(src),
        "topic2": topic(dst),
        "topic3": topic(owner) if kind == WITHDRAW else None,
        "data": "0x" + f"{first:064x}" + (f"{second:064x}" if second is not None else ""),
    }


def document(cutoff="2026-10-31"):
    metrics = {r["id"]: metric("0") for r in layout()}
    metrics["external-receipts"] = metric("100")
    metrics["deposits"] = metric("-10")
    subtotals(metrics)
    summaries = [{"chain": "ethereum", "events": 2, "status": "available"}]
    return {
        "schema_version": "cash-flow-month.v1",
        "policy_version": load()["policy"],
        "prime": "spark",
        "period_start": cutoff[:7] + "-01",
        "cutoff": cutoff,
        "configuration_revision": load()["revision"],
        "metrics": metrics,
        "event_summaries": summaries,
        "event_digest": digest(summaries),
        "cash_event_digest": digest([]),
        "cash_event_count": 0,
        "coverage": {"missing_chains": [], "unclassified_events": 0},
    }


def publication(cutoff="2026-10-31", revision="a" * 64):
    d = document(cutoff)
    return {
        "revision_id": revision,
        "publication_order": 1,
        "prime": "spark",
        "cutoff": cutoff,
        "computed_at": "2026-11-05T00:00:00+00:00",
        "document": d,
        "policy_version": d["policy_version"],
        "document_hash": digest(d),
        "evidence_hash": digest([]),
        "code_version": "test",
        "configuration_revision": load()["revision"],
    }


def test_scope_and_expense_perimeter():
    c = load()
    assert len(c["wallets"]) == 10
    assert (
        c["wallets"]["ethereum"]["0x8a3c66b30e2bbe5c2b1cff1054d869171fb87766"]["role"]
        == "operating"
    )
    assert expense_inputs("2026-10")[:3] == (Decimal("928282"), Decimal("928282"), Decimal("0"))
    assert expense_inputs("2026-11")[:3] == (None, None, None)


def test_savings_cost_basis_follows_share_transfers_and_interest_is_not_financing():
    rows = [
        event(1, DEPOSIT, 100, 100),
        event(2, TRANSFER, 50, None, src=OWNER, dst=OTHER),
        event(3, WITHDRAW, 60, 50, src=OTHER, dst=OTHER, owner=OTHER),
    ]
    result = split(rows, [{"chain": "ethereum", "address": VAULT}])
    assert not result["failures"]
    withdrawal = result["events"][-1]
    assert withdrawal["principal_units"] == "50" and withdrawal["interest_units"] == "10"
    assert result["states"]["ethereum:" + VAULT]["principal_units"] == "50"


def test_empty_withdrawals_are_valid_but_missing_history_and_duplicate_events_are_not():
    result = split([event(1, WITHDRAW, 0, 0)], [{"chain": "ethereum", "address": VAULT}])
    assert not result["failures"]
    for rows in [
        [event(1, WITHDRAW, 100, 100)],
        [event(1, DEPOSIT, 100, 100), event(1, DEPOSIT, 100, 100)],
    ]:
        assert split(rows, [{"chain": "ethereum", "address": VAULT}])["failures"]


def test_partial_known_zero_is_not_a_complete_zero_or_a_missing_value():
    unknown = metric(reason="missing")
    partial = total([("a", 1, metric("0")), ("b", 1, unknown)])
    assert (
        partial["status"] == "partial"
        and partial["known_value"] == "0"
        and partial["value"] is None
    )
    assert total([("b", 1, unknown)])["status"] == "unavailable"
    assert (
        total([("loss", 1, metric("-1.12345678901234567890")), ("cash", 1, metric("1"))])["value"]
        == "-0.12345678901234567890"
    )


def test_monthly_subtotals_and_evidence_digest_are_validated():
    d = document()
    validate(d)
    for target in ["sum", "hash", "scope", "decimal"]:
        broken = deepcopy(d)
        if target == "sum":
            broken["metrics"]["net-cash-change"]["value"] = "42"
        if target == "hash":
            broken["event_digest"] = "b" * 64
        if target == "scope":
            broken["prime"] = "grove"
        if target == "decimal":
            broken["metrics"]["external-receipts"]["value"] = "NaN"
        with pytest.raises(ValueError):
            validate(broken)


def test_annual_selection_pins_absence_and_rejects_cross_month_substitutions():
    p = publication()
    doc = annual("spark", 2026, date(2026, 11, 4), [p])
    selection = decode(doc["selection"], "spark", 2026)
    newer = publication("2026-11-04", "b" * 64)
    retained = annual("spark", 2026, date(2026, 11, 4), [p, newer], selection=selection)
    assert retained == doc and doc["periods"][10]["revision"] is None
    assert (
        next(r for r in doc["lines"] if r["id"] == "external-receipts")["values"]["2026-12"][
            "status"
        ]
        == "future"
    )
    with pytest.raises(LookupError):
        annual("spark", 2026, date(2026, 11, 4), [], selection=selection)
    bad = deepcopy(p)
    bad["cutoff"] = "2026-09-30"
    with pytest.raises(ValueError):
        annual("spark", 2026, date(2026, 11, 4), [bad], selection=selection)


def test_closed_month_requires_eom_and_balance_totals_do_not_fall_back():
    doc = annual("spark", 2026, date(2026, 11, 4), [publication("2026-10-27")])
    assert doc["periods"][9]["revision"] is None
    doc = annual("spark", 2026, date(2026, 11, 4), [publication()])
    assert next(r for r in doc["lines"] if r["id"] == "closing-cash")["total"]["value"] is None
    assert (
        next(r for r in doc["lines"] if r["id"] == "external-receipts")["total"]["known_value"]
        == "100"
    )


class Reader:
    def close(self):
        pass

    def cash_flow_year(self, prime, year, revisions=None, as_of=None):
        return [publication()]

    def cash_flow_events(self, prime, month, revision, line, offset, limit):
        if month != "2026-10" or revision != "a" * 64:
            raise LookupError("not found")
        return {"events": [], "total_count": 0}


def test_api_reads_only_stored_publications_and_weak_etags(monkeypatch):
    from settle.api import revenue

    monkeypatch.setattr(revenue, "today", lambda: date(2026, 11, 5))
    with TestClient(create_app(reader=Reader())) as client:
        response = client.get("/v3/reporting/cash-flow", params={"prime": "spark", "year": 2026})
        assert response.status_code == 200
        assert (
            client.get(
                "/v3/reporting/cash-flow",
                params={"prime": "spark", "year": 2026},
                headers={"If-None-Match": "W/" + response.headers["etag"]},
            ).status_code
            == 304
        )
        for params in [
            {"prime": "grove", "year": 2026},
            {"prime": "spark", "year": 2025},
            {"prime": "spark", "year": 2026, "cutoff": "2026-11-05"},
            {"prime": "spark", "year": 2026, "revision": "invalid"},
        ]:
            assert client.get("/v3/reporting/cash-flow", params=params).status_code in (404, 422)
        assert (
            client.get(
                "/v3/reporting/cash-flow/events",
                params={
                    "prime": "spark",
                    "month": "2026-10",
                    "revision": "a" * 64,
                    "line": "unclassified",
                },
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/v3/reporting/cash-flow/events",
                params={"prime": "spark", "month": "2026-09", "revision": "a" * 64},
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/v3/reporting/cash-flow/events",
                params={
                    "prime": "spark",
                    "month": "2026-10",
                    "revision": "a" * 64,
                    "line": "wrong",
                },
            ).status_code
            == 404
        )


def test_actual_msc_receipts_are_split_once_and_owned_safes_are_internal():
    from datetime import UTC, datetime

    from settle.revenue.cash_flow.classify import collect_ledger

    config = load()
    chain = "ethereum"
    usds = next(t for t, m in config["tokens"][chain].items() if m["symbol"] == "USDS")
    subproxy = "0x3300f198988e4c9c63f75df86de36421f06af8c4"
    safe = "0x8a3c66b30e2bbe5c2b1cff1054d869171fb87766"
    owner = next(a for a, m in config["wallets"][chain].items() if m["label"] == "Spark Foundation")

    def cash(i, sender, receiver, amount):
        e = event(i, TRANSFER, amount * 10**18, None, src=sender, dst=receiver)
        e.update(address=usds, timestamp=int(datetime(2026, 2, 2, tzinfo=UTC).timestamp()))
        return e

    rows = [
        cash(1, ZERO, subproxy, 7071339),
        cash(2, ZERO, subproxy, 7071339),
        cash(3, owner, safe, 5),
    ]
    ledger = collect_ledger({"chains": {chain: {"events": rows}}}, config=config)["entries"]
    demand = [r for r in ledger if r["line_id"] == "sky-demand-receipts"]
    assert len(demand) == 1 and demand[0]["amount_usd"] == "3117284"
    assert (
        sum(
            Decimal(r["amount_usd"])
            for r in ledger
            if r["source_event_id"].endswith(":0") and r["tx_hash"] == rows[0]["tx_hash"]
        )
        == 7071339
    )
    assert [r["line_id"] for r in ledger if r["tx_hash"] == rows[2]["tx_hash"]] == []
    assert [r["line_id"] for r in ledger if r["tx_hash"] == rows[1]["tx_hash"]] == ["unclassified"]


def test_no_observed_scope_is_unavailable_instead_of_a_known_zero():
    from settle.revenue.cash_flow.classify import cash_metrics

    config = load()
    evidence = {"chains": {ch: {"status": "unavailable"} for ch in config["wallets"]}}
    result = cash_metrics(evidence, {"entries": [], "savings": {"failures": {}}}, config)
    assert result["deposits"]["status"] == "unavailable"
    assert result["buybacks"]["known_value"] is None
    assert result["sky-contributions"]["status"] == "unavailable"
    assert result["opening-cash"]["known_value"] is None
    assert result["unclassified-net"]["known_value"] is None
    token = next(t for t, m in config["tokens"]["ethereum"].items() if m["symbol"] == "USDS")
    owner = next(iter(config["wallets"]["ethereum"]))
    evidence["chains"]["ethereum"] = {
        "status": "available",
        "balances": [
            {
                "token": token,
                "owner": owner,
                "opening": str(12 * 10**18),
                "closing": str(15 * 10**18),
            }
        ],
    }
    known = cash_metrics(evidence, {"entries": [], "savings": {"failures": {}}}, config)
    assert known["opening-cash"]["known_value"] == "12"
    assert known["closing-cash"]["known_value"] == "15"
    assert known["measured-change"]["known_value"] == "3"


def test_source_evidence_rejects_wrong_config_truncated_balances_and_out_of_period_logs():
    from settle.revenue.cash_flow.evidence import validate_evidence

    config = load()
    e = {
        "cutoff": "2026-10-31",
        "configuration_revision": config["revision"],
        "chains": {
            ch: {"chain": ch, "status": "unavailable", "events": [], "balances": []}
            for ch in config["wallets"]
        },
    }
    validate_evidence(e, config)
    wrong = deepcopy(e)
    wrong["configuration_revision"] = "a" * 64
    with pytest.raises(ValueError):
        validate_evidence(wrong, config)
    wrong = deepcopy(e)
    wrong["chains"]["ethereum"].update(
        status="available",
        pins={"opening": {"block": 1}, "closing": {"block": 2, "before_genesis": False}},
    )
    with pytest.raises(ValueError, match="balance scope"):
        validate_evidence(wrong, config)
    wrong = deepcopy(e)
    wrong["chains"]["ethereum"]["events"] = [{}]
    with pytest.raises(ValueError, match="Unavailable"):
        validate_evidence(wrong, config)


def test_full_month_expenses_are_not_paired_with_mtd_or_stale_income(monkeypatch):
    from settle.revenue.cash_flow.build import build

    config = load()

    def inputs(cutoff, actual):
        evidence = {
            "cutoff": cutoff,
            "configuration_revision": config["revision"],
            "chains": {
                ch: {"chain": ch, "status": "unavailable", "events": [], "balances": []}
                for ch in config["wallets"]
            },
        }
        income = {
            "lines": [
                {"id": id, "values": {"2026-10": metric("100", recognition="accrual")}}
                for id in ["statement.net_revenue", "statement.gains", "funding.savings"]
            ],
            "periods": [{"key": "2026-10", "actual_cutoff": actual}],
            "report_hash": "a" * 64,
            "selection": "retained-income",
        }
        return build(evidence, income)[0]["metrics"]

    partial = inputs("2026-10-15", "2026-10-15")
    assert all(
        partial[k]["status"] == "unavailable"
        for k in ["net-income", "expenses-booked", "token-incentives"]
    )
    stale = inputs("2026-10-31", "2026-10-27")
    assert all(
        stale[k]["status"] == "unavailable"
        for k in ["net-income", "non-stable-gains", "other-non-cash"]
    )
    complete = inputs("2026-10-31", "2026-10-31")
    assert complete["net-income"]["status"] == "partial"
    assert Decimal(complete["net-income"]["known_value"]) == Decimal("-928182")
    assert complete["contributor-spk"]["status"] == "unavailable"


def test_delayed_mtd_sources_keep_monthly_and_ytd_totals_provisional():
    p = publication("2026-11-01")
    d = annual("spark", 2026, date(2026, 11, 4), [p])
    assert d["periods"][10]["stale"] is True
    row = next(r for r in d["lines"] if r["id"] == "external-receipts")
    assert row["values"]["2026-11"]["status"] == "partial"
    assert row["values"]["2026-11"]["known_value"] == "100"
    assert row["total"]["status"] == "partial"
    original = annual("spark", 2026, date(2026, 11, 1), [p])
    row = next(r for r in original["lines"] if r["id"] == "external-receipts")
    assert row["values"]["2026-11"]["status"] == "available"


@pytest.mark.parametrize(
    "component", ["sky_staking_income_component", "steakhouse_income_component"]
)
def test_msc_receivable_excludes_non_msc_operating_income_reversals(component):
    p = publication("2026-01-31")
    cells = p["document"]["metrics"]
    cells["earned-not-received"] = metric("-120", recognition="accrual")
    cells["earned-not-received"]["approved_msc_component"] = metric("100", recognition="accrual")
    cells["earned-not-received"][component] = metric("20", recognition="accrual")
    cells["sky-allocation-receipts"] = metric("60")
    subtotals(cells)
    report = annual("spark", 2026, date(2026, 1, 31), [p])
    assert report["methodology"]["msc_receivable_basis"] == "approved_msc_component"
    got = next(r for r in report["lines"] if r["id"] == "msc-receivable")["values"]["2026-01"]
    assert Decimal(got["value"]) == Decimal(load()["opening_msc_receivable"]) + 40
    # The actual operating bridge remains unchanged; its non-MSC reversal is needed.
    assert (
        next(r for r in report["lines"] if r["id"] == "earned-not-received")["values"]["2026-01"][
            "value"
        ]
        == "-120"
    )


def test_msc_receivable_does_not_infer_an_entitlement_from_mixed_bridge_without_approval():
    p = publication("2026-01-31")
    cells = p["document"]["metrics"]
    cells["earned-not-received"] = metric("-20", recognition="accrual")
    cells["earned-not-received"]["steakhouse_income_component"] = metric(
        "20", recognition="accrual"
    )
    report = annual("spark", 2026, date(2026, 1, 31), [p])
    got = next(r for r in report["lines"] if r["id"] == "msc-receivable")["values"]["2026-01"]
    assert got["status"] == "partial"
    assert Decimal(got["known_value"]) == Decimal(load()["opening_msc_receivable"])


def test_legacy_msc_projection_does_not_advertise_a_changed_basis():
    report=annual("spark",2026,date(2026,1,31),[publication("2026-01-31")])
    assert "msc_receivable_basis" not in report["methodology"]
