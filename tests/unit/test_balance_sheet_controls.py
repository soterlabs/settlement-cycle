import gzip
import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from settle.revenue import balance_sheet_controls as controls
from settle.revenue.balance_sheet import annual, decode
from settle.revenue.balance_sheet_sources import Reader
from settle.revenue.balance_sheet_store import validate
from settle.revenue.verification import digest

VAULT = "0x" + "1" * 40
OWNER = "0x" + "2" * 40


def proof(monkeypatch, shares=0, failure=False, deployed=True, gross_available=True):
    def rpc(chain, method, params):
        if method == "eth_getCode":
            return "0x01" if deployed else "0x"
        if failure:
            raise ValueError("RPC unavailable")
        data = params[0]["data"]
        if data.startswith("0x70a08231"):
            value = shares if data.endswith(OWNER[2:]) else 0
        elif data.startswith("0x07a2d13a"):
            value = shares * 2
        elif data.startswith("0x18160ddd"):
            value = 100
        else:
            value = 200  # totalAssets
        return "0x" + format(value, "064x")

    monkeypatch.setattr("settle.revenue.balance_sheet_sources.rpc", rpc)
    reader = Reader({"ethereum": {"block": 1}})
    liabilities = {
        "S56": {
            "chain": "ethereum",
            "token": VAULT,
            "status": "available" if gross_available else "unavailable",
            "value": "200",
        }
    }
    control = controls.collect(
        reader,
        [("S56", "ethereum", VAULT)],
        {
            "revision": "a" * 64,
            "wallets": {"ethereum": {OWNER: {}}},
        },
        liabilities,
    )
    doc = dict(
        pins=reader.pins,
        reads=reader.reads,
        liabilities=liabilities,
    )
    return control, doc


def test_zero_is_proved_for_every_owner_and_vault_itself(monkeypatch):
    control, doc = proof(monkeypatch)
    assert controls.validate(control, doc)["value"] == "0"
    assert {r["owner"] for r in control["observations"]} == {OWNER, VAULT}
    assert len(control["observations"]) == 2


@pytest.mark.parametrize("kwargs", [{"shares": 1, "gross_available": False}, {"failure": True}])
def test_missing_valuation_and_failed_reads_never_create_guessed_netting(monkeypatch, kwargs):
    control, doc = proof(monkeypatch, **kwargs)
    controls.validate(control, doc)
    assert control["status"] == "unavailable"
    assert control["value"] is None


def test_predeployment_zero_requires_empty_code(monkeypatch):
    control, doc = proof(monkeypatch, deployed=False)
    controls.validate(control, doc)
    doc["reads"][0]["result"] = "0x01"
    with pytest.raises(ValueError, match="lack raw chain proof"):
        controls.validate(control, doc)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "unproved_zero",
        "different_vault",
        "missing_pin",
        "conflicting_raw",
        "empty_scope",
        "fabricated_available",
    ],
)
def test_corrupt_or_incomplete_zero_proofs_are_rejected(monkeypatch, mutation):
    control, doc = proof(monkeypatch)
    if mutation == "missing":
        control["observations"].pop()
    elif mutation == "duplicate":
        control["observations"].append(deepcopy(control["observations"][0]))
    elif mutation == "unproved_zero":
        doc["reads"][-1]["result"] = "0x" + format(1, "064x")
    elif mutation == "different_vault":
        doc["liabilities"]["S56"]["token"] = OWNER
    elif mutation == "missing_pin":
        doc["pins"] = {}
    elif mutation == "conflicting_raw":
        doc["reads"].append({**doc["reads"][-1], "result": "0x" + format(1, "064x")})
    elif mutation == "empty_scope":
        control["owners"]["ethereum"] = []
    elif mutation == "fabricated_available":
        control["observations"][0]["shares"] = None
    with pytest.raises(ValueError):
        controls.validate(control, doc)


@pytest.mark.parametrize("internal_shares", [0, 1000000])
def test_retained_no_control_is_unchanged_and_new_control_resolves_only_savings(
    monkeypatch, internal_shares
):
    path = Path(__file__).parents[1] / "fixtures" / "spark_closing_stocks_october.json.gz"
    doc = json.load(gzip.open(path))
    record = dict(
        cutoff=doc["cutoff"],
        revision_id="a" * 64,
        publication_order=1,
        document_hash=digest(doc),
        document=doc,
    )
    inputs = dict(venues=[], cash=[], components=[], stocks=[record])
    before = annual(2026, date(2026, 10, 31), inputs)
    pinned = decode(before["selection"], "spark", 2026)
    assert (
        next(r for r in before["lines"] if r["id"] == "savings")["values"]["2026-10"]["status"]
        == "partial"
    )

    codes = {
        (r["chain"], r["contract"]): r["result"]
        for r in doc["reads"]
        if r["method"] == "eth_getCode"
    }

    def rpc(ch, method, params):
        if method == "eth_getCode":
            return codes.get((ch, params[0]), "0x01")
        data = params[0]["data"]
        vault = doc["liabilities"]["S56"]["token"]
        if ch == "ethereum" and params[0]["to"] == vault:
            if data.startswith("0x70a08231"):
                return "0x" + format(internal_shares if data.endswith(vault[2:]) else 0, "064x")
            if data.startswith("0x07a2d13a"):
                return "0x" + format(internal_shares, "064x")
            if data.startswith("0x18160ddd"):
                return "0x" + format(10**18, "064x")
            for r in doc["reads"]:
                if r["chain"] == ch and r["contract"] == vault and r.get("data") == data:
                    return r["result"]
        return "0x" + "0" * 64

    monkeypatch.setattr("settle.revenue.balance_sheet_sources.rpc", rpc)
    reader = Reader(doc["pins"])
    vaults = [(k, v["chain"], v["token"]) for k, v in doc["liabilities"].items()]
    control = controls.collect(
        reader, vaults, {"revision": "a" * 64, "wallets": {}}, doc["liabilities"]
    )
    updated = deepcopy(doc)
    updated["reads"].extend(reader.reads)
    updated["savings_control"] = control
    updated["proof_hash"] = digest({k: v for k, v in updated.items() if k != "proof_hash"})
    validate(updated)
    newer = {
        **record,
        "revision_id": "b" * 64,
        "publication_order": 2,
        "document": updated,
        "document_hash": digest(updated),
    }
    inputs["stocks"].append(newer)
    assert annual(2026, date(2026, 10, 31), inputs, selection=pinned) == before
    after = annual(2026, date(2026, 10, 31), inputs)
    rows = {r["id"]: r["values"]["2026-10"] for r in after["lines"]}
    old = {r["id"]: r["values"]["2026-10"] for r in before["lines"]}
    assert rows["savings"]["status"] == "available"
    from decimal import Decimal, localcontext

    with localcontext() as ctx:
        ctx.prec = 100
        elimination = Decimal(control["value"])
        assert (
            Decimal(rows["savings"]["value"])
            == Decimal(old["savings"]["known_value"]) - elimination
        )
        assert Decimal(rows["savings.eliminations"]["value"]) == -elimination
        assert (elimination > 0) == bool(internal_shares)
    assert rows["total_liabilities"]["status"] == "partial"  # payables still unknown
    assert {k: v for k, v in rows["total_assets"].items() if k != "source_refs"} == {
        k: v for k, v in old["total_assets"].items() if k != "source_refs"
    }
    assert rows["total_equity"] == old["total_equity"]


def test_internal_claim_uses_asset_conversion_and_same_liability_usd_mark(monkeypatch):
    control, doc = proof(monkeypatch, shares=3)
    assert controls.validate(control, doc)["value"] == "6"
    assert control["observations"][1]["shares"] == "3"
    # A changed gross USD mark cannot silently retain the old elimination.
    doc["liabilities"]["S56"]["value"] = "300"
    with pytest.raises(ValueError, match="valuation differs"):
        controls.validate(control, doc)


def test_claims_cannot_exceed_total_issuance(monkeypatch):
    control, _doc = proof(monkeypatch, shares=101)
    # Collection keeps valuation unavailable when assets exceed gross liability.
    assert control["status"] == "unavailable"


def test_claim_value_is_context_independent():
    from decimal import localcontext

    with localcontext() as ctx:
        ctx.prec = 6
        got = controls.claim_value(
            1138408001475, 207131500431691, {"status": "available", "value": "207131500.431691"}
        )
    assert got == "1138408.001475"


def test_saved_january_chain_claim_is_eliminated_not_escrow_cash():
    from settle.revenue.balance_sheet import GROUPS
    from settle.revenue.cash_flow.classify import is_cash
    from settle.revenue.cash_flow.config import load

    path = Path(__file__).parents[1] / "fixtures" / "spark_savings_control_january.json.gz"
    doc = json.load(gzip.open(path))
    control = controls.validate(doc["savings_control"], doc)
    assert control["value"] == "1138408.001475"
    positive = [r for r in control["observations"] if r["shares"] != "0"]
    assert len(positive) == 1
    assert positive[0]["owner"] == "0x2e1b01adabb8d4981863394bea23a1263cbaedfc"
    assert positive[0]["shares"] == "1125108436796"
    assert positive[0]["assets_units"] == "1138408001475"
    cfg = load()
    asset_venues = {v for _, _, vs in GROUPS for v in vs}
    for row in control["observations"]:
        assert row["venue"] not in asset_venues
        if row["owner"] in cfg["wallets"][row["chain"]]:
            assert not is_cash(cfg, row["chain"], row["vault"], row["owner"])
