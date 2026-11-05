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

    history = [
        e(1, DEPOSIT, 100_000000, 100),
        e(2, TRANSFER, 50, src=a, dst=b),
        e(3, WITHDRAW, 60_000000, 50, src=b, dst=b, owner=b),
    ]
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
    extra["chains"]["ethereum"]["events"][1]["topic1"] = topic("0x" + "9" * 40)
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
