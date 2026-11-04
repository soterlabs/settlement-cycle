from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from settle.domain.config import load_prime_by_id
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.revenue.financial import decode_selection, statements
from settle.revenue.spark_monthly_income import anchorage
from settle.revenue.spark_onchain import topic
from settle.revenue.spark_reporting_store import validate_document
from settle.revenue.spark_savings import collect_vault


def test_published_monthly_returns_fill_history_without_principal_or_changing_v38():
    report = statements("spark", 2026, date(2026, 10, 31), [], spark_statement=True)
    lookup = {r["id"]: r for r in report["lines"]}
    assert report["policy_version"] == "prime-management.spark-history.v3.9"
    assert lookup["investment.maple_syrup_usdc"]["values"]["2026-01"]["value"] == "850905.46"
    assert lookup["investment.venue.S14"]["values"]["2026-01"]["quality"] == "estimate"
    assert lookup["investment.rwa.buidl"]["values"]["2026-01"]["value"] == "0.00"
    selection = decode_selection(report["selection"], "spark", 2026)
    selection["policy_version"] = "prime-management.spark-statement.v3.8"
    old = statements("spark", 2026, date(2026, 10, 31), [], selection=selection)
    old_lookup = {r["id"]: r for r in old["lines"]}
    assert old_lookup["investment.maple_syrup_usdc"]["values"]["2026-01"]["value"] is None
    assert (
        statements(
            "spark",
            2026,
            date(2026, 10, 31),
            [],
            selection=decode_selection(old["selection"], "spark", 2026),
        )
        == old
    )
    ledger = next(
        r
        for r in report["spark_allocation_ledger"]
        if r["month"] == "2026-01" and r["venue_id"] == "S14"
    )
    assert ledger["native_revenue_usd"] == lookup["investment.venue.S14"]["values"]["2026-01"]


def test_undeployed_eth_savings_is_proved_zero_without_inventing_a_usd_quote():
    prime = load_prime_by_id("spark")
    venue = next(v for v in prime.venues if v.id == "S58")
    source = SimpleNamespace(
        prime=prime,
        period=SimpleNamespace(start=date(2026, 1, 1), end=date(2026, 1, 31), n_days=31),
        opening_pins={"ethereum": 10},
        closing_pins={"ethereum": 20},
        block=lambda chain, day: 10 if day.month == 12 else 20,
        code=lambda *args: "0x",
        logs=lambda *args: [],
    )
    row = collect_vault(source, venue)
    assert row["value"] == "0" and row["quality"] == "supported"
    assert row["daily_translation"] == []
    doc = {
        "schema_version": "spark-onchain.v1",
        "period_start": "2026-01-01",
        "cutoff": "2026-01-31",
        "savings_interest": {"S58": row},
    }
    validate_document(doc)
    broken = deepcopy(doc)
    broken["savings_interest"]["S58"]["absence_proof"]["opening_code"] = "0x01"
    with pytest.raises(ValueError):
        validate_document(broken)


def test_anchorage_known_rollover_keeps_only_the_confirmed_interest():
    prime = load_prime_by_id("spark")
    usdc = next(v for v in prime.venues if v.id == "S26")
    holder = prime.alm[usdc.chain].hex
    payer = next(v for v in prime.venues if v.id == "S23").settlement_view.payer
    row = LogRow(
        10,
        1,
        1789084800,
        usdc.token.address.hex,
        TRANSFER_TOPIC0,
        topic(payer),
        topic(holder),
        None,
        "0x" + f"{10024418639471:064x}",
        "0x" + "1" * 64,
    )
    # September 11's documented 10M principal plus separate 24,410 income.
    from datetime import UTC, datetime

    row = __import__("dataclasses").replace(
        row, block_time=int(datetime(2026, 9, 11, tzinfo=UTC).timestamp())
    )
    source = SimpleNamespace(prime=prime, logs=lambda *args: [row])
    result = anchorage(source)
    assert Decimal(result["value"]) == 24410
    assert Decimal(result["principal_excluded_usd"]) == Decimal("10000008.639471")


def test_historical_cutoff_before_first_real_chain_block_uses_verified_genesis(monkeypatch):
    from settle.revenue import spark_onchain as onchain
    from datetime import UTC, datetime

    first = int(datetime(2026, 4, 30, tzinfo=UTC).timestamp())
    monkeypatch.setattr(onchain, "chain_origin", lambda chain: (0, first))
    monkeypatch.setattr(onchain, "_reporting_head", lambda chain: 1000)

    def forbidden(*args):
        raise AssertionError("Do not binary-search a chain that has not produced its first block")

    monkeypatch.setattr(onchain.hypersync, "find_block_at_or_before", forbidden)
    assert onchain.certified_boundary.__wrapped__("robinhood", date(2026, 1, 31), 128) == 0


def test_monthly_issuer_rejects_incomplete_daily_coverage():
    doc = {"schema_version": "spark-onchain.v1", "period_start": "2026-01-01", "cutoff": "2026-01-31", "issuer_income": {"pyusd": {"program": "pyusd", "status": "available", "value": "1", "daily": [{"date": "2026-01-01", "accrual": "1", "components": [{"accrual": "1"}]}]}}}
    with pytest.raises(ValueError, match="coverage"):
        validate_document(doc)
