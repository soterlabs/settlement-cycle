"""Actual historical share/quote evidence: accruals telescope to realized yield."""

import gzip
import json
from copy import deepcopy
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import pytest

from settle.revenue.cash_flow.classify import collect_ledger
from settle.revenue.cash_flow.collector import TRANSFER
from settle.revenue.cash_flow.config import load
from settle.revenue.spark_steakhouse import project, validate_document

FIXTURE = Path(__file__).parents[1] / "fixtures" / "spark_steakhouse_2026.json.gz"


def samples():
    return json.loads(gzip.decompress(FIXTURE.read_bytes()))["months"]


def test_real_monthly_accruals_equal_lifetime_yield_and_exclude_principal():
    expected = {
        "2026-01-31": "914.079957",
        "2026-02-28": "2602.160431",
        "2026-03-31": "2802.664010",
        "2026-04-30": "627.177037",
    }
    results = []
    for cutoff, amount in expected.items():
        result = project(samples()[cutoff]["proof"])
        assert D(result["value"]) == D(amount)
        assert result["recognition"] == "accrual" and result["quality"] == "supported"
        results.append(result)
    assert sum(D(r["value"]) for r in results) == D("6946.081435")
    assert sum(D(c["amount_usd"]) for c in results[0]["cash_events"]) == D("-1107900")
    returned = results[-1]["cash_events"]
    assert sum(D(c["amount_usd"]) for c in returned) == D("1114846.081435")
    assert sum(
        D(p["amount_usd"])
        for c in returned
        for p in c["parts"]
        if p["line_id"] == "allocation-changes"
    ) == D("1107900")
    assert sum(
        D(p["amount_usd"])
        for c in returned
        for p in c["parts"]
        if p["line_id"] == "external-receipts"
    ) == D("6946.081435")


def raw_cash(record):
    sign = -1 if record["direction"] == "subscription" else 1
    return dict(
        chain="ethereum",
        address=record["token"],
        block=record["block"],
        log_index=record["log_index"],
        tx_hash=record["tx_hash"],
        timestamp=record["timestamp"],
        topic0=TRANSFER,
        topic1="0x" + record["from"][2:].zfill(64),
        topic2="0x" + record["to"][2:].zfill(64),
        topic3=None,
        data="0x" + f"{int(D(record['amount_usd']) * 10**6 * sign):064x}",
    )


@pytest.mark.parametrize("cutoff", ["2026-01-31", "2026-04-30"])
def test_retained_cash_witness_overrides_expenses_and_provisional_gross_redemptions(cutoff):
    result = project(samples()[cutoff]["proof"])
    cash = [dict(r, source_ref="a" * 64) for r in result["cash_events"]]
    evidence = {"chains": {"ethereum": {"events": [raw_cash(r) for r in cash]}}}
    ledger = collect_ledger(evidence, config=load(), vault_cash=cash)["entries"]
    assert all(r["quality"] == "supported" for r in ledger)
    assert not any(r["line_id"] == "expenses-paid" for r in ledger)
    assert all(r["principal_evidence"]["source_revision"] == "a" * 64 for r in ledger)
    with localcontext() as ctx:
        ctx.prec = 100
        assert sum(D(r["amount_usd"]) for r in ledger) == sum(D(r["amount_usd"]) for r in cash)
    if cutoff.startswith("2026-04"):
        assert sum(D(r["amount_usd"]) for r in ledger if r["line_id"] == "external-receipts") == D(
            "6946.081435"
        )


@pytest.mark.parametrize(
    "kind",
    [
        "missing_deposit",
        "missing_cash",
        "balance",
        "quote",
        "duplicate_funding",
        "foreign_event",
        "pins",
    ],
)
def test_reconstruction_rejects_incomplete_or_changed_history(kind):
    raw = deepcopy(samples()["2026-04-30"])
    proof = raw["proof"]
    if kind == "missing_deposit":
        proof["history"].pop(1)
    elif kind == "missing_cash":
        proof["cash_events"].pop()
    elif kind == "balance":
        proof["opening"]["positions"][0]["shares_units"] = "1"
    elif kind == "quote":
        proof["closing"]["positions"][0]["assets_units"] = "100"
    elif kind == "duplicate_funding":
        extra = deepcopy(proof["cash_events"][0])
        extra["log_index"] += 1000
        proof["cash_events"].append(extra)
    elif kind == "foreign_event":
        proof["history"][0]["address"] = "0x" + "f" * 40
    else:
        proof["closing"]["block"] = proof["opening"]["block"]
    with pytest.raises(ValueError):
        project(proof)


def test_published_income_and_cash_splits_are_recomputed_from_retained_proof():
    raw = deepcopy(samples()["2026-04-30"])
    document = dict(
        period_start="2026-04-01",
        cutoff="2026-04-30",
        opening_pins={"ethereum": raw["proof"]["opening"]["block"]},
        closing_pins={"ethereum": raw["proof"]["closing"]["block"]},
        steakhouse=raw,
    )
    validate_document(document)
    raw["value"] = "1114846.081435"
    with pytest.raises(ValueError, match="income or cash split"):
        validate_document(document)


def test_new_income_row_is_accrual_and_enters_morpho_revenue_once():
    from settle.revenue.financial_spark_review import prepare
    from settle.revenue.statement_rollups import definitions
    from tests.unit.test_spark_review_november import statement

    result = samples()["2026-01-31"]
    statement_input = statement()
    statement_input["periods"][0]["expected_cutoff"] = "2026-01-31"
    saved = dict(cutoff="2026-01-31", revision_id="a" * 64, document={"steakhouse": result})
    prepare(statement_input, [saved])
    leaf = next(r for r in statement_input["lines"] if r["id"] == "investment.steakhouse_usdc")
    assert leaf["values"]["2026-01"]["recognition"] == "accrual"
    assert D(leaf["values"]["2026-01"]["value"]) == D("914.079957")
    totals = definitions(
        "spark",
        statement_input["lines"],
        allocation_rewards=True,
        complete_spark=True,
        spark_income=True,
    )
    for name in ["statement.interest", "statement.family.morpho_vaults"]:
        row = next(r for r in totals if r["id"] == name)
        assert [c["id"] for c in row["components"]].count("investment.steakhouse_usdc") == 1


def test_closed_position_returns_supported_zero_without_invented_yield():
    raw = project(samples()["2026-11-08"]["proof"])
    assert raw["value"] == "0" and raw["opening_value"] == "0" and raw["closing_value"] == "0"
    assert raw["cash_events"] == []


def test_failed_refresh_keeps_reconstructed_income_and_proof():
    from settle.revenue.spark_reporting_store import carry_forward

    raw = deepcopy(samples()["2026-01-31"])
    old = dict(
        period_start="2026-01-01",
        cutoff="2026-01-31",
        steakhouse=raw,
        opening_pins={"ethereum": raw["proof"]["opening"]["block"]},
        closing_pins={"ethereum": raw["proof"]["closing"]["block"]},
    )
    new = {**old, "steakhouse": {"status": "unavailable"}}
    retained = carry_forward(new, dict(document=old, cutoff="2026-01-31", revision_id="a" * 64))
    assert retained["steakhouse"]["status"] == "partial"
    assert retained["steakhouse"]["value"] == raw["value"]
    assert retained["steakhouse"]["proof_hash"] == raw["proof_hash"]
    validate_document(retained)
