from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from settle.revenue.cash_flow.accuracy import SCHEMA, principal_splits, specs
from settle.revenue.cash_flow.classify import collect_ledger
from settle.revenue.cash_flow.collector import DEPOSIT, TRANSFER, WITHDRAW
from settle.revenue.cash_flow.config import load
from settle.revenue.cash_flow.receivable import entitlement
from tests.unit.test_cash_flow import event, topic


def sample():
    c = load()
    spec = next(v for v in specs(c) if v["venue"] == "S10")
    owners = list(c["wallets"]["ethereum"])
    a, b = owners[:2]

    def e(i, kind, first, second=None, src=a, dst=a, owner=a):
        v = event(i, kind, first, second, src=src, dst=dst, owner=owner)
        v["address"] = spec["address"]
        return v

    zero = "0x" + "0" * 40
    history = [
        e(1, TRANSFER, 100, src=zero, dst=a),
        e(2, DEPOSIT, 100_000000, 100),
        e(3, TRANSFER, 50, src=a, dst=b),
        e(4, TRANSFER, 50, src=b, dst=zero),
        e(5, WITHDRAW, 60_000000, 50, src=b, dst=b, owner=b),
    ]
    history[0]["block"] = history[1]["block"]
    history[1]["log_index"] = 1
    history[3]["block"] = history[4]["block"]
    history[4]["log_index"] = 2
    history[0]["tx_hash"] = history[1]["tx_hash"]
    history[3]["tx_hash"] = history[4]["tx_hash"]
    supplement = {
        "schema_version": SCHEMA,
        "configuration_revision": c["revision"],
        "cutoff": "2026-01-31",
        "chains": {
            "ethereum": {
                "status": "available",
                "block": 100,
                "verified": [spec],
                "failures": {},
                "events": history,
                "share_balances": [
                    {"vault": spec["address"], "owner": h, "units": "50" if h == a else "0"}
                    for h in c["wallets"]["ethereum"]
                ],
            }
        },
    }
    evidence = {
        "cutoff": "2026-01-31",
        "chains": {"ethereum": {"pins": {"closing": {"block": 100}}, "events": []}},
    }
    return c, spec, a, b, supplement, evidence


def test_redemption_principal_follows_owned_share_transfers_and_cash_yield_is_separate():
    c, spec, _a, b, extra, ev = sample()
    splits, failures = principal_splits(extra, c, ev)
    assert not failures
    item = next(iter(splits.values()))
    assert Decimal(item["principal_units"]) == 50_000000
    assert Decimal(item["interest_units"]) == 10_000000
    transfer = deepcopy(extra["chains"]["ethereum"]["events"][-1])
    transfer.update(
        address=spec["asset"],
        log_index=1,
        topic0=TRANSFER,
        topic1=topic(spec["address"]),
        topic2=topic(b),
        data="0x" + f"{60_000000:064x}",
    )
    ev["chains"]["ethereum"]["events"] = [transfer]
    ev["accuracy"] = extra
    ledger = collect_ledger(ev, config=c)
    assert ledger["verified_redemptions"] == 1
    assert {r["line_id"]: Decimal(r["amount_usd"]) for r in ledger["entries"]} == {
        "allocation-changes": Decimal(50),
        "external-receipts": Decimal(10),
    }
    assert sum(Decimal(r["amount_usd"]) for r in ledger["entries"]) == 60


def test_unknown_external_shares_fail_closed_without_certifying_redemptions():
    c, _spec, _a, _b, extra, ev = sample()
    extra["chains"]["ethereum"]["events"][2]["topic1"] = topic("0x" + "9" * 40)
    split, failures = principal_splits(extra, c, ev)
    assert not split and failures
    changed = deepcopy(extra)
    changed["cutoff"] = "2026-02-28"
    with pytest.raises(ValueError, match="scope"):
        principal_splits(changed, c, ev)
    changed = deepcopy(extra)
    changed["chains"]["ethereum"]["verified"][0]["asset"] = "0x" + "9" * 40
    with pytest.raises(ValueError, match="Unconfigured"):
        principal_splits(changed, c, ev)


def test_internal_cash_relay_requires_unique_same_asset_amount_and_transaction():
    c, spec, a, b, _, ev = sample()
    relay = "0x" + "9" * 40
    token = spec["asset"]
    rows = []
    for i, (src, dst) in enumerate([(a, relay), (relay, b)], 1):
        e = event(i, TRANSFER, 5_000000, None, src=src, dst=dst)
        e.update(address=token, tx_hash="0x" + "8" * 64, block=1, log_index=i)
        rows.append(e)
    ev["chains"]["ethereum"]["events"] = rows
    r = collect_ledger(ev, config=c)["entries"]
    assert len(r) == 2 and all(v["line_id"] == "internal-routing" for v in r)
    assert sum(Decimal(v["amount_usd"]) for v in r) == 0
    rows[1]["data"] = "0x" + f"{4_000000:064x}"
    assert not any(
        v["line_id"] == "internal-routing" for v in collect_ledger(ev, config=c)["entries"]
    )


def test_msc_receivable_uses_net_entitlement_and_explicit_correction_periods():
    jan = entitlement(date(2026, 1, 31))
    assert jan["value"] == "1387824" and jan["quality"] == "supported"
    june = entitlement(date(2026, 6, 30))
    assert june["value"] == "4100523"
    july = entitlement(date(2026, 7, 31))
    assert july["value"] == "9567225"  # regular July + executed corrections for prior periods
    oct = entitlement(date(2026, 10, 31))
    assert oct["status"] == "partial" and oct["known_value"] == "2392355"
    assert entitlement(date(2026, 10, 1))["status"] == "unavailable"
    assert entitlement(date(2026, 11, 4))["status"] == "unavailable"


def test_fee_share_mints_and_truncated_semantic_histories_never_certify_a_basis():
    c, _spec, _a, _b, extra, ev = sample()
    broken = deepcopy(extra)
    broken["chains"]["ethereum"]["events"].pop(0)
    assert principal_splits(broken, c, ev)[1]
    broken = deepcopy(extra)
    fee = deepcopy(broken["chains"]["ethereum"]["events"][0])
    fee.update(log_index=999, tx_hash="0x" + "7" * 64)
    broken["chains"]["ethereum"]["events"].append(fee)
    next(r for r in broken["chains"]["ethereum"]["share_balances"] if r["owner"] == _a)["units"] = (
        "150"
    )
    split, failures = principal_splits(broken, c, ev)
    assert not split and any("fee or missing" in r for r in failures.values())


def test_authenticated_morpho_fee_shares_have_no_cash_subscription_principal():
    from settle.revenue.cash_flow.fee_evidence import ACCRUE_INTEREST

    c, _spec, a, _b, extra, ev = sample()
    source = extra["chains"]["ethereum"]
    fee = deepcopy(source["events"][0])
    fee.update(
        block=2, log_index=8, tx_hash="0x" + "6" * 64, topic2=topic(a), data="0x" + f"{10:064x}"
    )
    accrual = deepcopy(fee)
    accrual.update(
        log_index=9, topic0=ACCRUE_INTEREST, data="0x" + f"{110_000000:064x}" + f"{10:064x}"
    )
    source["events"].insert(2, fee)
    next(r for r in source["share_balances"] if r["owner"] == a)["units"] = "60"
    source["fee_events"] = [accrual]
    split, failures = principal_splits(extra, c, ev)
    assert not failures
    row = next(iter(split.values()))
    assert Decimal(row["principal_units"]) < 50_000000
    assert Decimal(row["principal_units"]) + Decimal(row["interest_units"]) == 60_000000
    corrupted = deepcopy(extra)
    corrupted["chains"]["ethereum"]["fee_events"][0]["data"] = (
        "0x" + f"{110_000000:064x}" + f"{11:064x}"
    )
    assert principal_splits(corrupted, c, ev)[1]


def test_savings_cash_principal_and_interest_remain_separate_from_allocation_proofs():
    c = load()
    v = next(v for v in c["savings"] if v["venue"] == "S56")
    user = "0x" + "8" * 40
    deposit = event(1, DEPOSIT, 100_000000, 100, src=user, dst=user)
    deposit["address"] = v["address"]
    withdrawal = event(2, WITHDRAW, 110_000000, 100, src=user, dst=user, owner=user)
    withdrawal["address"] = v["address"]
    cash = []
    for sem, src, dst, units in [
        (deposit, user, v["address"], 100_000000),
        (withdrawal, v["address"], user, 110_000000),
    ]:
        row = deepcopy(sem)
        row.update(
            address=v["asset"],
            topic0=TRANSFER,
            topic1=topic(src),
            topic2=topic(dst),
            data="0x" + f"{units:064x}",
        )
        cash.append(row)
    ledger = collect_ledger(
        {"chains": {"ethereum": {"events": cash, "savings_events": [deposit, withdrawal]}}},
        config=c,
    )
    assert (
        sum(Decimal(r["amount_usd"]) for r in ledger["entries"] if r["line_id"] == "deposits") == 0
    )
    assert (
        sum(Decimal(r["amount_usd"]) for r in ledger["entries"] if r["line_id"] == "expenses-paid")
        == -10
    )


def test_earlier_deployment_subset_reuses_complete_later_history(tmp_path, monkeypatch):
    import gzip
    import json

    from settle.revenue.cash_flow import accuracy
    from settle.revenue.verification import digest

    c = load()
    vs = [v for v in specs(c) if v["venue"] in ["S10", "S65"]]
    monkeypatch.setattr(accuracy, "specs", lambda config: vs)
    monkeypatch.setenv("CASH_FLOW_CAPTURE_PERSIST", "0")

    def getter(chain, method, params):
        spec = next(v for v in vs if v["address"] == params[0]["to"])
        if spec["venue"] == "S65":
            raise ValueError("Not deployed at historical cutoff")
        return topic(spec["asset"])

    monkeypatch.setattr(accuracy, "rpc", getter)
    monkeypatch.setattr(
        accuracy.hypersync,
        "query_logs",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Must reuse complete later history")),
    )
    monkeypatch.setattr(accuracy.fee_evidence, "collect", lambda *a: [])
    monkeypatch.setattr(accuracy, "share_balances", lambda *a: [])
    root = tmp_path / "accuracy"
    root.mkdir()
    revision = digest({"schema": SCHEMA, "configuration": c["revision"]})
    source = {
        "status": "available",
        "block": 100,
        "specs": vs,
        "verified": vs,
        "failures": {},
        "events": [],
    }
    with gzip.open(root / f"{revision}-ethereum-2026-11-04.json.gz", "wt") as f:
        json.dump(source, f)
    e = {
        "cutoff": "2026-05-31",
        "chains": {"ethereum": {"status": "available", "pins": {"closing": {"block": 50}}}},
    }
    r = accuracy.collect(e, c, tmp_path)
    assert r["chains"]["ethereum"]["status"] == "available"
    assert [v["venue"] for v in r["chains"]["ethereum"]["verified"]] == ["S10"]


def test_partial_local_cache_is_never_read_as_a_complete_capture(tmp_path):
    from settle.revenue.cash_flow.collector import read_capture, write_capture

    path = tmp_path / "capture.json.gz"
    assert read_capture(path) is None
    write_capture(path, {"status": "available", "public_event": "verified"})
    assert read_capture(path)["public_event"] == "verified"
    path.write_bytes(path.read_bytes()[:12])
    assert read_capture(path) is None
    assert not list(tmp_path.glob("*.tmp"))
