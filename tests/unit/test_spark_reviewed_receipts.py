"""Regression checks against captured public Ethereum logs, not invented receipts."""

import gzip
import json
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from settle.revenue.cash_flow.classify import collect_ledger
from settle.revenue.cash_flow.collector import TRANSFER
from settle.revenue.cash_flow.config import load
from settle.revenue.cash_flow.liability import address
from settle.revenue.cash_flow.reviewed import reviewed_events
from settle.revenue.spark_sky_staking import FARM, URN, USDS, project

FIXTURE = Path(__file__).parents[1] / "fixtures" / "spark_reviewed_receipts_2026.json.gz"


def public_rows():
    receipts = json.loads(gzip.decompress(FIXTURE.read_bytes()))["receipts"]
    return [
        dict(
            chain="ethereum",
            address=r["address"].lower(),
            block=tx["block"],
            log_index=int(r["logIndex"], 16),
            tx_hash=tx["tx_hash"],
            timestamp=tx["timestamp"],
            topic0=r["topics"][0],
            topic1=r["topics"][1] if len(r["topics"]) > 1 else None,
            topic2=r["topics"][2] if len(r["topics"]) > 2 else None,
            topic3=r["topics"][3] if len(r["topics"]) > 3 else None,
            data=r["data"],
        )
        for tx in receipts
        for r in tx["logs"]
        if r["topics"]
    ]


def cash_rows():
    config = load()
    owners = config["wallets"]["ethereum"]
    tokens = config["tokens"]["ethereum"]
    return [
        r
        for r in public_rows()
        if r["topic0"] == TRANSFER
        and r["address"] in tokens
        and (address(r["topic1"]) in owners or address(r["topic2"]) in owners)
    ]


def ledger(rows=None, config=None):
    return collect_ledger(
        {"chains": {"ethereum": {"events": cash_rows() if rows is None else rows}}},
        config=config or load(),
    )["entries"]


def test_actual_reviewed_cash_partitions_conserve_every_original_log():
    entries = ledger()
    records = reviewed_events()["records"]
    with localcontext() as ctx:
        ctx.prec = 100
        for record in records:
            matches = [
                e
                for e in entries
                if e["tx_hash"] == record["tx_hash"] and e["log_index"] == record["log_index"]
            ]
            assert sum(D(e["amount_usd"]) for e in matches) == D(record["amount_usd"])
            assert [(e["line_id"], e["amount_usd"]) for e in matches] == [
                (p["line_id"], p["amount_usd"]) for p in record["parts"]
            ]
            assert all(e["classification_evidence"]["source_revision"] for e in matches)
    assert len({e["id"] for e in entries}) == len(entries)


def test_principal_disposals_and_swap_are_not_earned_cash_or_expenses():
    entries = ledger()
    for payer in [
        "0xa29e963992597b21bcdcaa969d571984869c4ff5",
        "0xbeef3f3a04e28895f3d5163d910474901981183d",
    ]:
        rows = [e for e in entries if payer in (e["from"], e["to"])]
        assert rows and all(e["line_id"] == "allocation-changes" for e in rows)
    vault = [e for e in entries if e["from"].startswith("0xbeef3f3a")]
    assert len(vault) == 2 and all(e["quality"] == "estimate" for e in vault)
    swaps = [
        e
        for e in entries
        if e["tx_hash"] == "0xa1dafc1c7b0b36ccca9f50801addf3efcf115f29d5c8e4a7eb7f9cabdcba8cbf"
    ]
    assert sum(D(e["amount_usd"]) for e in swaps if e["line_id"] == "internal-routing") == 0
    assert not any(e["line_id"] == "expenses-paid" for e in swaps)
    assert sum(D(e["amount_usd"]) for e in swaps if e["line_id"] == "unclassified") == D(
        "-2.902265232988598019"
    )
    otc = [e for e in entries if e["from"] == "0x1851c64bbfad132cbe75481f1690c381288ea492"]
    assert {e["line_id"]: D(e["amount_usd"]) for e in otc} == {
        "allocation-changes": D("2000"),
        "unclassified": D("249.42819"),
    }
    assert sum(D(e["amount_usd"]) for e in entries if e["line_id"] == "treasury-disposals") == D(
        "382428.427806741322142644"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("tx_hash", "0x" + "f" * 64),
        ("block", 1),
        ("timestamp", 1),
        ("address", USDS),
        ("topic1", "0x" + "0" * 64),
        ("topic2", "0x" + "0" * 64),
        ("data", "0x" + f"{1:064x}"),
    ],
)
def test_changed_cash_facts_never_receive_reviewed_classification(field, value):
    record = next(
        r
        for r in reviewed_events()["records"]
        if r["from"] == "0xc8a3e1e0776b912047c89dc16470fd9c7ea1141d"
    )
    event = next(
        r
        for r in cash_rows()
        if r["tx_hash"] == record["tx_hash"] and r["log_index"] == record["log_index"]
    )
    changed = {**event, field: value}
    assert not any(e.get("classification_evidence") for e in ledger([changed]))


def test_unknown_future_payer_receipts_and_legacy_policy_remain_unchanged():
    event = next(
        r
        for r in cash_rows()
        if address(r["topic1"]) == "0xc8a3e1e0776b912047c89dc16470fd9c7ea1141d"
    )
    future = {**event, "tx_hash": "0x" + "e" * 64}
    assert ledger([future])[0]["line_id"] == "unclassified"
    assert ledger([event], load(review=False))[0]["line_id"] == "unclassified"


def staking_rows():
    return [
        SimpleNamespace(
            **{
                **r,
                "block_number": r["block"],
                "block_time": r["timestamp"],
                "transaction_hash": r["tx_hash"],
            }
        )
        for r in public_rows()
        if r["address"] in (FARM, USDS)
    ]


def test_actual_staking_reward_requires_paid_funding_and_owned_delivery():
    owners = list(load()["wallets"]["ethereum"])
    result = project(staking_rows(), date(2026, 3, 31), owners)
    assert result["value"] == "2095.067540312875692237"
    assert result["recognition"] == "receipt" and len(result["receipts"]) == 1
    receipt = result["receipts"][0]
    assert receipt["from"] == URN and receipt["to"] == "0x8a3c66b30e2bbe5c2b1cff1054d869171fb87766"
    without_funding = [
        r for r in staking_rows() if not (r.address == USDS and address(r.topic1) == FARM)
    ]
    with pytest.raises(ValueError, match="unique farm-funded"):
        project(without_funding, date(2026, 3, 31), owners)
    with pytest.raises(ValueError, match="unique farm-funded"):
        project(staking_rows(), date(2026, 3, 31), [])


def test_only_new_retained_sources_add_staking_income_and_old_selections_keep_their_shape():
    from settle.revenue.financial_spark_review import prepare
    from settle.revenue.statement_rollups import definitions
    from tests.unit.test_spark_review_november import statement

    old = statement()
    prepare(old, [None])
    assert not any(r["id"] == "investment.sky_staking" for r in old["lines"])
    s = statement()
    s["periods"] = [dict(key="2026-03", state="complete", expected_cutoff="2026-03-31")]
    for r in s["lines"]:
        r["values"] = {"2026-03": r["values"]["2026-01"]}
    source = project(staking_rows(), date(2026, 3, 31), list(load()["wallets"]["ethereum"]))
    saved = dict(cutoff="2026-03-31", revision_id="a" * 64, document={"sky_staking": source})
    prepare(s, [saved])
    row = next(r for r in s["lines"] if r["id"] == "investment.sky_staking")
    assert row["values"]["2026-03"]["value"] == source["value"]
    assert row["values"]["2026-03"]["recognition"] == "receipt"
    totals = definitions(
        "spark", s["lines"], allocation_rewards=True, complete_spark=True, spark_income=True
    )
    interest = next(r for r in totals if r["id"] == "statement.interest")
    assert sum(c["id"] == "investment.sky_staking" for c in interest["components"]) == 1
    assert len(s["sky_staking_cash_receipts"]) == 1


def test_staking_income_and_receipt_change_operating_cash_only_once(monkeypatch):
    from settle.revenue.cash_flow import build as module
    from settle.revenue.cash_flow.config import layout
    from settle.revenue.cash_flow.model import amount, metric

    config = load()
    evidence = dict(
        cutoff="2026-03-31",
        configuration_revision=config["revision"],
        chains={
            chain: dict(chain=chain, status="unavailable", events=[], balances=[])
            for chain in config["wallets"]
        },
    )
    claim = D("2095.067540312875692237")
    received = D(0)

    def metrics(*args):
        result = {r["id"]: metric("0") for r in layout()}
        result["external-receipts"] = metric(received)
        result["measured-change"] = metric("0")
        return result

    monkeypatch.setattr(module, "cash_metrics", metrics)

    def report(staking):
        values = {
            "statement.net_revenue": D(1000) + (claim if staking else 0),
            "statement.gains": D(0),
            "funding.savings": D(0),
        }
        if staking:
            values["investment.sky_staking"] = claim
        income = dict(
            lines=[dict(id=k, values={"2026-03": metric(v)}) for k, v in values.items()],
            periods=[dict(key="2026-03", actual_cutoff="2026-03-31")],
            report_hash="a" * 64,
            selection="test",
        )
        return module.build(evidence, income)[0]["metrics"]

    before = report(False)
    received = claim
    after = report(True)
    assert amount(after["net-operating"]) - amount(before["net-operating"]) == claim
    received = D(0)
    missing_delivery = report(True)
    assert amount(missing_delivery["net-operating"]) == amount(before["net-operating"])


def test_retained_staking_claim_scope_and_total_are_validated_and_failed_refresh_keeps_known_claim():
    from copy import deepcopy

    from settle.revenue.spark_reporting_store import carry_forward
    from settle.revenue.spark_sky_staking import validate_document

    source = project(staking_rows(), date(2026, 3, 31), list(load()["wallets"]["ethereum"]))
    block = source["receipts"][0]["block"]
    doc = dict(
        period_start="2026-03-01",
        cutoff="2026-03-31",
        sky_staking=source,
        opening_pins={"ethereum": block - 1},
        closing_pins={"ethereum": block + 1},
    )
    validate_document(doc)
    for field, value in [("value", "1"), ("scope", {}), ("period_end", "2026-04-01")]:
        bad = deepcopy(doc)
        bad["sky_staking"][field] = value
        with pytest.raises(ValueError):
            validate_document(bad)
    old = deepcopy(doc)
    old["cutoff"] = "2026-03-30"
    old["sky_staking"]["period_end"] = "2026-03-30"
    new = {**doc, "sky_staking": {"status": "unavailable"}}
    carried = carry_forward(new, dict(document=old, cutoff="2026-03-30", revision_id="a" * 64))
    assert carried["sky_staking"]["status"] == "partial"
    assert carried["sky_staking"]["value"] == source["value"]
    assert carried["sky_staking"]["period_end"] == "2026-03-30"
    validate_document(carried)


def test_future_staking_delivery_needs_retained_reward_witness():
    from copy import deepcopy
    from datetime import UTC, datetime

    from settle.revenue.cash_flow.collector import event

    new_hash = "0x" + "d" * 64
    changed = []
    for row in staking_rows():
        item = deepcopy(row)
        item.transaction_hash = new_hash
        item.block_time = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())
        changed.append(item)
    owners = list(load()["wallets"]["ethereum"])
    claim = project(changed, date(2026, 10, 31), owners)
    raw = [
        event(r, "ethereum")
        for r in changed
        if r.topic0 == TRANSFER and address(r.topic1) == URN and address(r.topic2) in owners
    ]
    evidence = {"chains": {"ethereum": {"events": raw}}}
    assert collect_ledger(evidence)["entries"][0]["line_id"] == "unclassified"
    receipts = [dict(r, source_ref="a" * 64) for r in claim["receipts"]]
    got = collect_ledger(evidence, income_receipts=receipts)["entries"]
    assert len(got) == 1 and got[0]["line_id"] == "external-receipts"
    assert got[0]["classification_evidence"]["source_revision"] == "a" * 64
    with pytest.raises(ValueError, match="Duplicate income"):
        collect_ledger(evidence, income_receipts=receipts * 2)


def test_authenticated_redemption_cost_basis_overrides_provisional_gross_booking(monkeypatch):
    from settle.revenue.cash_flow import classify

    record = next(r for r in reviewed_events()["records"] if r.get("basis_pending"))
    row = next(
        r
        for r in cash_rows()
        if r["tx_hash"] == record["tx_hash"] and r["log_index"] == record["log_index"]
    )
    units = int(row["data"], 16)
    witness = dict(
        chain="ethereum",
        tx_hash=row["tx_hash"],
        vault=record["from"],
        asset=row["address"],
        receiver=record["to"],
        assets_units=str(units),
        principal_units=str(units - 1000000),
        interest_units="1000000",
        log_index=12345,
        history_digest="b" * 64,
    )
    monkeypatch.setattr(classify, "principal_splits", lambda *args: ({"known": witness}, {}))
    got = ledger([row])
    assert {r["line_id"]: D(r["amount_usd"]) for r in got} == {
        "allocation-changes": D(record["amount_usd"]) - 1,
        "external-receipts": D(1),
    }
    assert all("principal_evidence" in r for r in got)


@pytest.mark.parametrize("alter", [False, True])
def test_reviewed_cash_conversion_requires_both_exact_legs(alter):
    from copy import deepcopy

    tx = "0xa1dafc1c7b0b36ccca9f50801addf3efcf115f29d5c8e4a7eb7f9cabdcba8cbf"
    rows = deepcopy([r for r in cash_rows() if r["tx_hash"] == tx])
    assert len(rows) == 2
    if alter:
        rows[0]["data"] = "0x" + f"{int(rows[0]['data'], 16) + 1:064x}"
    else:
        rows.pop()
    got = ledger(rows)
    assert got and all(r["line_id"] == "unclassified" for r in got)
    with localcontext() as ctx:
        ctx.prec = 100
        config = load()

        def signed(r):
            units = (
                D(int(r["data"], 16)) / 10 ** config["tokens"]["ethereum"][r["address"]]["decimals"]
            )
            return units * (1 if address(r["topic2"]) in config["wallets"]["ethereum"] else -1)

        assert sum(D(e["amount_usd"]) for e in got) == sum(signed(r) for r in rows)


def test_staking_transfer_without_reward_paid_is_not_certified_zero():
    from settle.revenue.spark_sky_staking import PAID

    rows = [r for r in staking_rows() if r.topic0 != PAID]
    assert project([], date(2026, 3, 31), list(load()["wallets"]["ethereum"]))["value"] == "0"
    with pytest.raises(ValueError, match="Unmatched SKY staking"):
        project(rows, date(2026, 3, 31), list(load()["wallets"]["ethereum"]))


def retained_staking_document():
    source = project(staking_rows(), date(2026, 3, 31), list(load()["wallets"]["ethereum"]))
    block = source["receipts"][0]["block"]
    return dict(
        period_start="2026-03-01",
        cutoff="2026-03-31",
        sky_staking=source,
        opening_pins={"ethereum": block - 1},
        closing_pins={"ethereum": block + 1},
    )


@pytest.mark.parametrize(
    "kind", ["before_opening", "after_closing", "missing_pins", "reused_proof"]
)
def test_retained_staking_rejects_wrong_pins_and_reused_reward_witnesses(kind):
    from copy import deepcopy

    from settle.revenue.spark_sky_staking import validate_document

    doc = retained_staking_document()
    validate_document(doc)
    receipt = doc["sky_staking"]["receipts"][0]
    if kind == "before_opening":
        receipt["block"] = doc["opening_pins"]["ethereum"]
    elif kind == "after_closing":
        receipt["block"] = doc["closing_pins"]["ethereum"] + 1
    elif kind == "missing_pins":
        doc.pop("opening_pins")
    else:
        duplicate = deepcopy(receipt)
        duplicate["log_index"] += (
            1000  # Another cash log cannot reuse this claim's farm/reward logs.
        )
        doc["sky_staking"]["receipts"].append(duplicate)
        with localcontext() as ctx:
            ctx.prec = 100
            doc["sky_staking"]["value"] = str(D(receipt["amount_usd"]) * 2)
    with pytest.raises(ValueError, match="SKY staking"):
        validate_document(doc)


@pytest.mark.parametrize("field", ["block_number", "block_time", "log_index"])
def test_staking_claim_legs_must_share_the_same_block_and_distinct_log_ids(field):
    from copy import deepcopy

    from settle.revenue.spark_sky_staking import PAID

    rows = deepcopy(staking_rows())
    reward = next(r for r in rows if r.address == FARM and r.topic0 == PAID)
    payment = next(
        r
        for r in rows
        if r.address == USDS
        and address(r.topic1) == URN
        and address(r.topic2) in load()["wallets"]["ethereum"]
    )
    setattr(
        payment, field, reward.log_index if field == "log_index" else getattr(payment, field) + 1
    )
    with pytest.raises(ValueError, match="claim legs differ"):
        project(rows, date(2026, 3, 31), list(load()["wallets"]["ethereum"]))
