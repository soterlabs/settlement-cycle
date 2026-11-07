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
    assert report["policy_version"] == "prime-management.spark-reconciled.v3.10"
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
    from datetime import UTC, datetime

    from settle.revenue import spark_onchain as onchain

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


def test_completion_rejects_a_changed_retained_chain_boundary():
    from settle.revenue.spark_reporting_job import complete_document
    source = SimpleNamespace(opening_pins={"ethereum": 11, "robinhood": 0}, closing_pins={"ethereum": 20, "robinhood": 1})
    old = {"document": {"opening_pins": {"ethereum": 10}, "closing_pins": {"ethereum": 20}}}
    with pytest.raises(ValueError, match="boundaries differ"):
        complete_document(old, source)


def test_completion_accepts_an_additional_certified_chain_without_mutating_old(monkeypatch):
    from settle.revenue.spark_reporting_job import complete_document
    monkeypatch.setattr("settle.revenue.spark_completion.collect", lambda *a, **k: {"statement_allocations": {}, "savings_interest": {}, "amm_fees": {}})
    monkeypatch.setattr("settle.revenue.spark_onchain.native_supply", lambda *a: {"status": "available", "value": "0"})
    monkeypatch.setattr("settle.revenue.spark_reporting_job.gains", lambda *a: {})
    monkeypatch.setattr("settle.revenue.spark_reconciliation.empty_avax_gain", lambda source, old: old)
    monkeypatch.setattr("settle.revenue.spark_reconciliation.net_sparklend_gain", lambda source, old, *a: old)
    monkeypatch.setattr("settle.revenue.spark_monthly_income.collect", lambda *a, **k: {"issuer_income": {}, "monthly_income_errors": {}})
    source = SimpleNamespace(opening_pins={"ethereum": 10, "robinhood": 0}, closing_pins={"ethereum": 20, "robinhood": 1})
    old = {"revision_id": "old", "document": {"opening_pins": {"ethereum": 10}, "closing_pins": {"ethereum": 20}, "native_supplies": {}, "gains": {}}}
    before = deepcopy(old)
    result = complete_document(old, source)
    assert result["opening_pins"] == source.opening_pins
    assert result["closing_pins"] == source.closing_pins
    assert old == before


def test_accounting_refresh_retains_reporting_chain_absent_from_revenue_snapshot(monkeypatch):
    from settle.revenue import spark_reporting_job as job
    opening = {chain: 10 for chain in (*job.WETH, "avalanche_c", "robinhood")}
    closing = {chain: 20 for chain in opening}
    revenue = {"revision_id": "revenue", "opening_pins": {k: v for k, v in opening.items() if k != "robinhood"},
               "closing_pins": {k: v for k, v in closing.items() if k != "robinhood"}}
    prior = {"revision_id": "reporting", "publication_order": 1, "cutoff": "2026-09-30",
             "document": {"opening_pins": opening, "closing_pins": closing, "native_supplies": {"retained": "unchanged"}}}
    before = deepcopy((revenue, prior))
    monkeypatch.setattr(job.store, "read", lambda *a, **k: revenue)
    monkeypatch.setattr(job.spark_reporting_store, "year_rows", lambda *a, **k: [prior])
    monkeypatch.setattr(job, "load_prime_by_id", lambda *a: None)
    monkeypatch.setattr(job, "certified_boundary", lambda *a: pytest.fail("Retained reporting pin was replaced"))
    monkeypatch.setattr("settle.revenue.spark_treasury.collect", lambda source: {"errors": {}})
    monkeypatch.setattr("settle.revenue.spark_ownership.collect", lambda source, **k: {"errors": {}})
    source = SimpleNamespace(opening_pins=opening, closing_pins=closing)
    result = job.complete_month(None, date(2026, 9, 30), source=source, accounting_only=True)
    assert result["opening_pins"] == opening
    assert result["closing_pins"] == closing
    assert result["native_supplies"] == {"retained": "unchanged"}
    assert result["treasury"] == result["ownership"] == {"errors": {}}
    assert (revenue, prior) == before
    revenue["closing_pins"]["ethereum"] = 21
    with pytest.raises(ValueError, match="different source boundaries"):
        job.complete_month(None, date(2026, 9, 30), source=source, accounting_only=True)
