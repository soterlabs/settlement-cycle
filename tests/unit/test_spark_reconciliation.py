from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from settle.domain.config import load_prime_by_id
from settle.extract.aave_reconstruct import BT_T0, MINT_T0, RAY
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.extract.uniswap_v3 import get_sqrt_ratio_at_tick
from settle.revenue import spark_reconciliation as rec
from settle.revenue.spark_onchain import topic
from settle.revenue.spark_v4_fees import principal_amounts


def row(index, token, event, first, second, words):
    return LogRow(
        11,
        index,
        int(datetime(2026, 10, 1, tzinfo=UTC).timestamp()),
        token,
        event,
        first,
        second,
        None,
        "0x" + "".join(f"{w:064x}" for w in words),
        "0x" + "1" * 64,
    )


def test_capital_bridge_excludes_large_deposit():
    result = rec.claim_bridge(100, 1105, 1000, scale=1, rows=[], basis="test")
    assert result["value"] == "5"
    assert result["reconciliation"]["net_capital_units"] == "1000"


def test_small_withdrawal_emitted_as_mint_uses_positive_half_up_rounding():
    holder, token = topic("0x" + "1" * 40), "0x" + "2" * 40
    event = row(1, token, MINT_T0, None, holder, [7, 10, 2 * RAY])
    assert rec.scaled_changes([event, event], holder)[0][1:] == (-2, 2 * RAY)
    transfer = row(2, token, BT_T0, holder, holder, [4, RAY])
    assert rec.scaled_changes([transfer], holder)[0][1] == 0


def test_vault_deposit_is_capital_and_unexplained_fee_mint_is_rejected(monkeypatch):
    prime = load_prime_by_id("spark")
    venue = next(v for v in prime.venues if v.id == "S32")
    token, holder = venue.token.address.hex, topic(prime.alm[venue.chain].hex)
    zero = topic("0x" + "0" * 40)
    events = [
        row(1, token, TRANSFER_TOPIC0, zero, holder, [100]),
        row(2, token, rec.DEPOSIT, None, holder, [100, 100]),
    ]
    monkeypatch.setattr(rec, "owner_logs", lambda *args, **kwargs: events)

    class Source:
        opening_pins, closing_pins = {"ethereum": 10}, {"ethereum": 20}

        def __init__(self):
            self.prime = prime

        def number(self, chain, token, signature, block, *args):
            if signature == "balanceOf(address)":
                return 100 if block == 10 else 200
            return 100 if block == 10 else 210

    unit_venue = replace(venue, underlying=replace(venue.underlying, decimals=0))
    result = rec.vault_supply(Source(), unit_venue)
    assert result["value"] == "10" and result["quality"] == "supported"
    events.append(row(3, token, TRANSFER_TOPIC0, zero, holder, [1]))
    with pytest.raises(ValueError, match="capital/share"):
        rec.vault_supply(Source(), unit_venue)


def test_fluid_rejects_even_roundtrip_share_movements(monkeypatch):
    prime = load_prime_by_id("spark")
    venue = next(v for v in prime.venues if v.id == "S36")
    monkeypatch.setattr(rec, "owner_logs", lambda *args, **kwargs: [SimpleNamespace(data="0x01")])
    with pytest.raises(ValueError, match="share flows"):
        rec.fluid_base(SimpleNamespace(prime=prime), venue)


def test_v4_principal_rounding_is_direction_sensitive():
    price = get_sqrt_ratio_at_tick(0)
    add = principal_amounts(price, -10, 10, 10001)
    remove = principal_amounts(price, -10, 10, -10001)
    assert all(a >= -b and a + b <= 1 for a, b in zip(add, remove, strict=True))
    assert any(a + b == 1 for a, b in zip(add, remove, strict=True))


def test_publisher_rejects_supply_principal_as_income_and_bad_translation():
    from settle.revenue.spark_reporting_store import validate_document

    doc = {
        "schema_version": "spark-onchain.v1",
        "period_start": "2026-10-01",
        "cutoff": "2026-10-31",
        "native_supplies": {
            "S70": rec.claim_bridge(100, 1105, 1000, scale=1, rows=[], basis="test")
        },
    }
    validate_document(doc)
    doc["native_supplies"]["S70"]["value"] = "1105"
    with pytest.raises(ValueError, match="revenue differs"):
        validate_document(doc)
    doc["native_supplies"]["S70"]["value"] = "5"
    doc["native_supplies"]["S70"]["translation_policy"] = rec.ETH_TRANSLATION
    doc["native_supplies"]["S70"]["daily_translation"] = []
    with pytest.raises(ValueError, match="translation days"):
        validate_document(doc)


def test_net_fx_offsets_depositor_exposure_and_excludes_new_capital():
    source = SimpleNamespace(
        period=SimpleNamespace(start=date(2026, 10, 1), n_days=1), price=lambda *args: Decimal(2000)
    )
    asset = {
        "date": "2026-10-01",
        "opening_claim_units": "120",
        "closing_claim_units": "205",
        "net_capital_units": "80",
        "interest_units": "5",
        "usd_price": "2100",
    }
    liability = {
        "date": "2026-10-01",
        "opening_claim_units": "100",
        "closing_claim_units": "208",
        "net_capital_units": "100",
        "interest_units": "8",
    }
    supply = {"status": "available", "daily_translation": [asset]}
    savings = {
        "status": "available",
        "daily_liability": [liability],
        "daily_translation": [{"date": "2026-10-01", "usd_price": "2100"}],
    }
    result = rec.net_sparklend_gain(source, {"status": "available"}, supply, savings)
    assert result["value"] == "2000"
    assert result["depositor_offset_usd"] == "10000"
    assert result["quality"] == "supported"
    liability["closing_claim_units"] = "999"
    with pytest.raises(ValueError, match="claim bridge"):
        rec.net_sparklend_gain(source, {"status": "available"}, supply, savings)


def test_real_october_reconciliation_changes_revenue_and_preserves_principal_bridges():
    import gzip
    import json
    from pathlib import Path

    from settle.revenue.financial import statements
    from settle.revenue.spark_reporting_store import validate_document

    evidence = json.loads(
        gzip.decompress(
            (
                Path(__file__).parents[1] / "fixtures/spark_october_reporting_reconciled.json.gz"
            ).read_bytes()
        )
    )
    document = evidence["components"][0]["document"]
    validate_document(document)
    report = statements(
        "spark",
        2026,
        date(2026, 10, 31),
        evidence["financial"],
        spark_publications=evidence["components"],
        spark_statement=True,
    )
    lookup = {r["id"]: r["values"]["2026-10"] for r in report["lines"]}

    def amount(key):
        row = lookup[key]
        return Decimal(row["value"] or row["known_value"])

    assert amount("investment.susds") == Decimal("2931213.884665323659491556")
    assert amount("investment.venue.S70") == Decimal("42216.857778309561307159")
    assert amount("investment.sparklend.weth") == Decimal("63434.2962679755668615143355823535473")
    assert amount("investment.morpho.sentora_rlusd") == Decimal("40810.009907014820654471")
    assert amount("gains.sparklend_nonstable") == Decimal("5981.2973346256055669352690302757942")
    assert (
        amount("investment.amm.uniswap_v4.usdt_usds") == 0
    )  # 100% Sky; new NFTs do not change this split.
    assert lookup["investment.amm.uniswap_v4.rlusd_usds"]["quality"] == "supported"
    assert lookup["gains.avax"]["quality"] == "supported"
    assert lookup["gains.eth_weth"]["quality"] == "estimate"
    assert lookup["demand.distribution_rewards"]["status"] == "unavailable"
    assert lookup["fees.sparklend"]["recognition"] == "receipt_in_kind"
    from decimal import localcontext

    with localcontext() as ctx:
        ctx.prec = 60
        assert amount("statement.interest") - amount("statement.funding") == amount(
            "statement.net_interest"
        )
    for venue in ("S61", "S62", "S66"):
        assert document["amm_fees"][venue]["capital_reconciliation"]["receipt_count"] == 119
    assert 460903 in document["amm_fees"]["S66"]["position_ids"]
    assert {485852, 489097} <= set(document["amm_fees"]["S62"]["position_ids"])


def test_real_v4_swap_cash_is_separate_from_lp_capital_and_changed_cash_fails(monkeypatch):
    import gzip
    import json
    from copy import deepcopy
    from decimal import localcontext
    from pathlib import Path

    from settle.revenue import spark_v4_fees as v4

    proof = json.loads(
        gzip.decompress(
            (
                Path(__file__).parents[1] / "fixtures/spark_october_v4_capital_receipt.json.gz"
            ).read_bytes()
        )
    )
    reads = {tuple(r["args"]): r["result"] for r in proof["reads"]}
    source = SimpleNamespace(
        prime=load_prime_by_id("spark"),
        opening_pins=proof["opening_pins"],
        closing_pins=proof["closing_pins"],
        call=lambda *args: reads[args],
    )
    receipt = deepcopy(proof["receipt"])
    monkeypatch.setattr(v4, "capital_receipt", lambda tx: receipt)
    monkeypatch.setattr(v4, "pool_swaps", lambda *args: [LogRow(**r) for r in proof["swaps"]])
    with localcontext() as ctx:
        ctx.prec = 60
        calculated = v4.reconcile_capital(source, deepcopy(proof["components"]))
        assert {
            k: r["capital_reconciliation"]["net_capital_added_usd"] for k, r in calculated.items()
        } == proof["expected_capital"]
        cash = next(
            r
            for r in receipt["logs"]
            if r["address"].lower() in v4.DECIMALS
            and r["topics"][0] == TRANSFER_TOPIC0
            and topic(v4.POOL) in r["topics"][1:]
        )
        cash["data"] = "0x" + f"{int(cash['data'], 16) + 1:064x}"
        with pytest.raises(ValueError, match="actual settled cash"):
            v4.reconcile_capital(source, deepcopy(proof["components"]))


def test_owner_evidence_reuses_durable_join_coverage(monkeypatch):
    source = SimpleNamespace(
        prime=load_prime_by_id("spark"),
        opening_pins={"ethereum": 10},
        closing_pins={"ethereum": 20},
    )
    calls = []

    def fetch(chain, selections, lo, hi, **kwargs):
        calls.append((chain, lo, hi, kwargs))
        return []

    monkeypatch.setattr(rec, "fetch_logs", fetch)
    assert rec.owner_logs(source, "ethereum", "0x" + "2" * 40, joined=True) == []
    assert calls[0][0:3] == ("ethereum", 11, 20)
    assert "join_mode" not in calls[0][3]
    assert "transaction_hash" in calls[0][3]["log_fields"]


def test_native_zero_requires_index_coverage_not_just_an_empty_response(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"items": [{"number": 20, "timestamp": "2026-10-01T00:00:00Z"}]}

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match="not finalized"):
        rec.avalanche_native_history.__wrapped__("0x" + "1" * 40, 10, 20)
