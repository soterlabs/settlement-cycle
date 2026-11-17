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
    inputs = dict(
        venues=[],
        cash=[],
        components=[
            {**record, "revision_id": "c" * 64, "document": {}, "document_hash": digest({})}
        ],
        stocks=[record],
    )
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
    updated["extra_assets"]["other_controlled_eth"] = dict(
        status="available", quality="supported", value="17.25", read_indices=[]
    )
    updated["extra_assets"]["other_token.LINK"] = dict(
        status="available", quality="supported", value="2.5", read_indices=[]
    )
    updated["extra_assets"]["other_token.EURC"] = dict(
        status="unavailable", value=None, reason="stale"
    )
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
    with localcontext() as ctx:
        ctx.prec = 100
        for key in ["nonstable", "total_assets"]:
            assert Decimal(rows[key]["known_value"] or rows[key]["value"] or "0") - Decimal(
                old[key]["known_value"] or old[key]["value"] or "0"
            ) == Decimal("19.75")
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


def test_other_eth_excludes_existing_inventories_and_uses_exact_units():
    from decimal import Decimal

    from settle.revenue.spark_treasury import OPS

    calls = []

    class Reader:
        def native(self, ch, owner):
            calls.append((ch, owner, "native"))
            return 10**18

        def balance(self, ch, token, owner):
            calls.append((ch, owner, "wrapped"))
            return 2 * 10**18

        def price(self, symbol):
            assert symbol == "ETH"
            return Decimal("2500")

    cfg = {
        "wallets": {
            "ethereum": {
                OWNER: {"role": "wallet"},
                VAULT: {"role": "savings_idle"},
                OPS: {"role": "wallet"},
                "alm": {"role": "wallet"},
                "intents": {"role": "intents"},
            },
            "avalanche_c": {OWNER: {"role": "wallet"}},
        }
    }
    assert controls.other_controlled_eth(Reader(), cfg, {"ethereum": "alm"}) == 10000
    assert calls == [
        ("ethereum", OWNER, "native"),
        ("ethereum", OWNER, "wrapped"),
        ("ethereum", OPS, "native"),
    ]


def test_other_eth_missing_rpc_is_not_silently_zero():
    class Reader:
        def native(self, *args):
            raise ValueError("Unavailable historical balance")

    with pytest.raises(ValueError, match="Unavailable"):
        controls.other_controlled_eth(
            Reader(), {"wallets": {"ethereum": {OWNER: {"role": "wallet"}}}}, {}
        )


@pytest.mark.parametrize(
    "symbol,answer,units,expected",
    [
        ("LINK", 1162904000, 308696891100682853602, "3589.84849448548493185180208"),
        ("EURC", 111726500, 128050000, "143.06578325"),
    ],
)
def test_reviewed_tokens_use_historical_feed_and_correct_currency(symbol, answer, units, expected):
    from decimal import Decimal

    token, scale, feed, _ = controls.OTHER_TOKENS[symbol]

    class Reader:
        def __init__(self):
            self.pins = {"ethereum": {"timestamp": 100000}}

        def balance(self, ch, tok, owner):
            assert tok == token and owner == OWNER
            return units

        def number(self, ch, address, signature):
            assert signature == "decimals()"
            return scale if address == token else 8

        def call(self, ch, address, signature):
            assert address == feed and signature == "latestRoundData()"
            return "0x" + "".join(f"{n:064x}" for n in [12, answer, 99000, 99000, 12])

    cfg = {
        "wallets": {"ethereum": {OWNER: {"role": "wallet"}, VAULT: {"role": "savings_idle"}}},
        "tokens": {"ethereum": {}},
    }
    assert controls.other_controlled_token(Reader(), cfg, symbol) == Decimal(expected)


@pytest.mark.parametrize(
    "fault", ["stale", "future", "negative", "unanswered", "scale", "malformed"]
)
def test_bad_other_token_marks_remain_unavailable(fault):
    token, _, _, max_age = controls.OTHER_TOKENS["LINK"]

    class Reader:
        def __init__(self):
            self.pins = {"ethereum": {"timestamp": 100000}}

        def balance(self, *args):
            return 10**18

        def number(self, ch, address, signature):
            return 18 if address == token else 9 if fault == "scale" else 8

        def call(self, *args):
            if fault == "malformed":
                return "0x"
            answer = 2**256 - 1 if fault == "negative" else 10**8
            updated = (
                100000 - max_age - 1 if fault == "stale" else 100001 if fault == "future" else 99000
            )
            return "0x" + "".join(
                f"{n:064x}"
                for n in [12, answer, updated, updated, 11 if fault == "unanswered" else 12]
            )

    cfg = {"wallets": {"ethereum": {OWNER: {"role": "wallet"}}}, "tokens": {"ethereum": {}}}
    with pytest.raises(ValueError):
        controls.other_controlled_token(Reader(), cfg, "LINK")


def test_other_token_already_in_cash_is_not_counted_again():
    token = controls.OTHER_TOKENS["LINK"][0]
    cfg = {
        "wallets": {"ethereum": {OWNER: {"role": "wallet"}}},
        "tokens": {"ethereum": {token: {"cash": True}}},
    }
    assert controls.other_controlled_token(object(), cfg, "LINK") == 0
