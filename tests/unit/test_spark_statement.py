from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from settle.revenue.financial import decode_selection, statements
from settle.revenue.financial_spark_statement import attach_rows
from settle.revenue.spark_savings import EVENTS, reconcile
from settle.revenue.spark_v4_fees import MOD, Q128, fee_delta, growth_inside
from tests.unit.test_financial_amms import source


def event(kind, block, first, second):
    return SimpleNamespace(
        block_number=block,
        log_index=0,
        topic0=next(k for k, v in EVENTS.items() if v == kind),
        data="0x" + f"{first:064x}{second:064x}",
        block_time=int(datetime(2026, 10, 2, tzinfo=UTC).timestamp()),
    )


def test_savings_drip_accrual_excludes_principal_and_opening_tail():
    ray = 10**27
    opening = {"supply": 100, "chi": ray, "assets": 102}
    # Deposit 100 at the new index, after paying interest on opening shares.
    rows = [event("drip", 11, ray * 110 // 100, 10), event("deposit", 12, 110, 100)]
    closing = {"supply": 200, "chi": ray * 110 // 100, "assets": 225}
    r = reconcile(opening, closing, rows, start=10, end=20)
    assert r["interest"] == 13  # 10 emitted + 5 closing tail - 2 opening tail
    assert r["deposits"] == 110 and r["rounding"] == 0
    with pytest.raises(ValueError):
        reconcile(opening, closing, rows + rows, start=10, end=20)
    with pytest.raises(ValueError):
        reconcile(opening, {**closing, "supply": 201}, rows, start=10, end=20)


def test_v4_fee_math_handles_ticks_and_uint256_wrap_without_capital():
    assert fee_delta(100, MOD - Q128, Q128) == 200
    assert growth_inside(100, 20, 30, 5, 0, 10) == 50
    assert growth_inside(100, 20, 30, -1, 0, 10) == (20 - 30) % MOD
    assert growth_inside(100, 20, 30, 10, 0, 10) == 10


def test_api_layout_every_nonsection_row_resolves_and_old_selection_replays():
    raw = source()
    old = statements("spark", 2026, date(2026, 10, 27), [raw], spark_amms=True)
    before = deepcopy(raw)
    new = statements("spark", 2026, date(2026, 10, 27), [raw], spark_statement=True)
    ids = {r["id"] for r in new["lines"]}
    assert all(row["metric_id"] in ids for row in new["statement_rows"] if row["kind"] != "section")
    assert all(
        not row["id"].startswith("allocation.anchorage.loan") for row in new["statement_rows"]
    )
    assert len({r["id"] for r in new["statement_rows"]}) == len(new["statement_rows"])
    assert new["display_decimals"] == 0
    assert (
        statements(
            "spark",
            2026,
            date(2026, 10, 27),
            [raw],
            spark_statement=True,
            selection=decode_selection(old["selection"], "spark", 2026),
        )
        == old
    )
    assert raw == before
    attach_rows(new)  # No duplicated metrics even if layout is reattached.
    assert len(ids) == len(new["lines"])


def test_savings_cost_enters_funding_once_and_no_deposits_enter_income():
    raw = source()
    row = {
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
            "savings_interest": {
                v: {
                    "status": "available",
                    "value": str(i + 1),
                    "recognition": "accrual",
                    "quality": "supported",
                }
                for i, v in enumerate(("S56", "S57", "S58", "S59", "S60", "S63"))
            },
        },
    }
    doc = statements(
        "spark", 2026, date(2026, 10, 27), [raw], spark_statement=True, spark_publications=[row]
    )
    lookup = {r["id"]: r for r in doc["lines"]}

    def amount(key):
        return Decimal(
            lookup[key]["values"]["2026-10"]["value"]
            or lookup[key]["values"]["2026-10"]["known_value"]
        )

    assert amount("funding.savings") == 21
    assert amount("statement.funding") - amount("funding.sky.net") == 21
    assert amount("statement.interest") - amount("statement.funding") == amount(
        "statement.net_interest"
    )
    assert lookup["demand.distribution_rewards"]["values"]["2026-10"]["value"] is None


def test_v4_pending_fees_are_earned_without_receipts_and_wrong_owner_is_rejected():
    from dataclasses import replace

    from settle.domain.config import load_prime_by_id
    from settle.domain.primes import Address
    from settle.extract._keccak import keccak256
    from settle.revenue.spark_v4_fees import MANAGER, collect

    prime = load_prime_by_id("spark")
    venue = next(v for v in prime.venues if v.id == "S66")
    venue = replace(venue, univ4_token_ids=(385168,))
    cfg = venue.univ4_pool_key
    encoded = (
        cfg.currency0.value.rjust(32, b"\0")
        + cfg.currency1.value.rjust(32, b"\0")
        + cfg.fee.to_bytes(32, "big")
        + cfg.tick_spacing.to_bytes(32, "big")
        + cfg.hooks.value.rjust(32, b"\0")
    )
    root = int.from_bytes(keccak256(keccak256(encoded) + (6).to_bytes(32, "big")), "big")
    position_key = keccak256(
        bytes.fromhex(MANAGER[2:])
        + bytes(3)
        + (1).to_bytes(3, "big")
        + (385168).to_bytes(32, "big")
    )
    slot = int.from_bytes(keccak256(position_key + ((root + 6) % MOD).to_bytes(32, "big")), "big")

    class Fake:
        period = SimpleNamespace(start=date(2026, 10, 1), end=date(2026, 10, 31))
        owner = prime.alm[venue.chain].hex

        def __init__(self):
            self.prime = prime

        def block(self, chain, day):
            return 10 if day.month == 9 else 20

        def logs(self, *args):
            return []

        def call(self, chain, contract, signature, block, *args):
            if signature == "getPoolAndPositionInfo(uint256)":
                return "0x" + encoded.hex() + f"{1 << 32:064x}"
            if signature == "ownerOf(uint256)":
                return "0x" + self.owner[2:].rjust(64, "0")
            key = args[0]
            value = (
                100
                if key == slot
                else 1 << 96
                if key == root
                else Q128
                if key in (root + 1, root + 2) and block == 20
                else 0
            )
            return "0x" + f"{value:064x}"

    source = Fake()
    result = collect(source, venue)
    assert Decimal(result["value"]) == Decimal("0.000000000000000200")
    assert result["collected_usd"] == "0"
    source.owner = "0x" + "1" * 40
    with pytest.raises(ValueError):
        collect(source, venue)
    with pytest.raises(ValueError):
        collect(
            Fake(),
            replace(venue, univ4_pool_key=replace(cfg, hooks=Address.from_str("0x" + "1" * 40))),
        )


def test_anchorage_receipt_attribution_excludes_position_principal():
    raw = source()
    raw["spark_statement_source_views"] = [
        {
            "venue_id": "S23",
            "enabled": True,
            "status": "unavailable",
            "cash_received_usd": "9",
            "principal_usd": "100000000",
            "position_value_usd": "100000009",
            "receipt_attribution": {
                "source_venue": "S26",
                "payer": "0x49506c3aa028693458d6ee816b2ec28522946872",
            },
        }
    ]

    def income():
        doc = statements("spark", 2026, date(2026, 10, 26), [raw], spark_statement=True)
        return next(r for r in doc["lines"] if r["id"] == "investment.anchorage")["values"][
            "2026-10"
        ]

    assert income()["value"] == "9" and income()["recognition"] == "receipt"
    raw["spark_statement_source_views"][0]["receipt_attribution"]["payer"] = "0x" + "1" * 40
    assert income()["value"] is None


def test_v4_earned_fees_are_added_once_and_issuer_rewards_stay_separate():
    raw = source()
    row = {
        "revision_id": "c" * 64,
        "publication_order": 1,
        "cutoff": "2026-10-26",
        "source": {
            "kind": "spark_onchain_components",
            "revision_id": "c" * 64,
            "result_hash": "d" * 64,
            "cutoff": "2026-10-26",
            "currency": "USD",
        },
        "document": {
            "schema_version": "spark-onchain.v1",
            "period_start": "2026-10-01",
            "cutoff": "2026-10-26",
            "amm_fees": {"S61": {"status": "available", "value": "5"}},
        },
    }
    raw.update(
        spark_statement_opening_pins={"ethereum": 1}, spark_statement_closing_pins={"ethereum": 2}
    )
    row["document"].update(opening_pins={"ethereum": 1}, closing_pins={"ethereum": 2})
    old = statements(
        "spark", 2026, date(2026, 10, 26), [raw], spark_amms=True, spark_publications=[row]
    )
    new = statements(
        "spark", 2026, date(2026, 10, 26), [raw], spark_statement=True, spark_publications=[row]
    )
    old_lookup = {r["id"]: r for r in old["lines"]}
    lookup = {r["id"]: r for r in new["lines"]}
    cell = lookup["investment.amm.uniswap_v4.pyusd_usds"]["values"]["2026-10"]
    assert cell["value"] == "15" and len(cell["source_refs"]) == 2
    assert cell["allocation_split"]["prime_income_usd"] == "15"
    assert (
        old_lookup["investment.amm.uniswap_v4.pyusd_usds"]["values"]["2026-10"]["known_value"]
        == "10"
    )
    for key in ("rewards.issuer.rlusd", "rewards.issuer.pyusd"):
        assert lookup[key]["values"] == old_lookup[key]["values"]


def test_price_retries_an_earlier_quote_instead_of_using_future_data(monkeypatch):
    from settle.revenue.spark_onchain import market_price

    timestamps = iter((1010, 980))
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "coins": {"test": {"price": 2, "timestamp": next(timestamps), "confidence": 0.99}}
            }

    def request(url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr("settle.revenue.spark_onchain.requests.get", request)
    assert market_price.__wrapped__("test", 1000) == {"price": "2", "timestamp": 980}
    assert len(calls) == 2


def test_arbitrum_bridged_susds_uses_verified_psm_rate_not_a_nonexistent_erc4626_getter(monkeypatch):
    monkeypatch.setattr("settle.revenue.spark_reconciliation.owner_logs", lambda *args, **kwargs: [])
    from settle.domain.config import load_prime_by_id
    from settle.revenue.spark_completion import arbitrum_susds

    prime = load_prime_by_id("spark")
    venue = next(v for v in prime.venues if v.id == "S43")

    class Fake:
        period = SimpleNamespace(start=date(2026, 10, 1), end=date(2026, 10, 1), n_days=1)

        def __init__(self):
            self.prime = prime

        def block(self, chain, day):
            return 10 if day.month == 9 else 20

        def number(self, chain, contract, signature, block, *args):
            return {
                "susds()": int(venue.token.address.hex, 16),
                "usds()": int(venue.underlying.address.hex, 16),
                "rateProvider()": 1,
                "getConversionRate()": 10**27 if block == 10 else 11 * 10**26,
                "balanceOf(address)": 100 * 10**18,
            }[signature]

    fake = Fake()
    fake.opening_pins = {"arbitrum": 10}
    fake.closing_pins = {"arbitrum": 20}
    result = arbitrum_susds(fake, venue)
    assert Decimal(result["value"]) == 10  # Revenue, not the 110 closing position.


def test_real_october_perimeter_is_complete_except_dr_and_savings_reconciles():
    import gzip
    import json
    from pathlib import Path

    from settle.revenue.spark_reporting_store import validate_document

    evidence = json.loads(
        gzip.decompress(
            (
                Path(__file__).parents[1] / "fixtures/spark_october_reporting_completion.json.gz"
            ).read_bytes()
        )
    )
    row = evidence["components"]
    validate_document(row["document"])
    document = statements(
        "spark",
        2026,
        date(2026, 10, 31),
        [evidence["financial"]],
        spark_statement=True,
        spark_publications=[row],
    )
    lines = {r["id"]: r for r in document["lines"]}

    def amount(key):
        c = lines[key]["values"]["2026-10"]
        return Decimal(c["value"] or c["known_value"])

    for row in document["statement_rows"]:
        if row["id"] == "expenses":
            break
        if row["metric_id"] is None:
            continue
        cell = lines[row["metric_id"]]["values"]["2026-10"]
        if row["id"] not in ("dr", "demand", "noninterest-income", "net-revenue"):
            assert cell["status"] == "available", (row, cell)
    assert amount("funding.savings") == Decimal("2946958.5856585491476617615859682550316")
    with __import__("decimal").localcontext() as ctx:
        ctx.prec = 60
        assert amount("statement.funding") == amount("funding.sky.net") + amount("funding.savings")
        assert amount("statement.net_interest") == amount("statement.interest") - amount(
            "statement.funding"
        )
    assert amount("investment.anchorage") == 967807
    assert lines["statement.net_revenue"]["values"]["2026-10"]["missing_components"] == [
        "demand.distribution_rewards"
    ]
    assert amount("investment.amm.uniswap_v4.rlusd_usds") == Decimal("12006.070624466859452765")


def test_saved_daily_pin_is_timestamp_checked_and_wrong_seed_falls_back(monkeypatch):
    from settle.revenue.spark_onchain import certified_saved_boundary

    target = int(datetime(2026, 10, 1, 23, 59, 59, tzinfo=UTC).timestamp())
    times = {10: target, 11: target + 1}
    monkeypatch.setattr("settle.extract.rpc.rpc_url", lambda chain: "https://example.invalid")
    monkeypatch.setattr(
        "settle.extract.rpc._post",
        lambda url, method, params: {
            "number": params[0],
            "timestamp": hex(times[int(params[0], 16)]),
        },
    )
    monkeypatch.setattr("settle.revenue.spark_onchain._reporting_head", lambda chain: 100)
    monkeypatch.setattr("settle.revenue.spark_onchain._reorg_margin", lambda: 2)
    monkeypatch.setattr("settle.revenue.spark_onchain.certified_boundary", lambda *args: 20)
    assert certified_saved_boundary.__wrapped__("ethereum", date(2026, 10, 1), 10) == 10
    times[10] = target + 1
    assert certified_saved_boundary.__wrapped__("ethereum", date(2026, 10, 1), 10) == 20
