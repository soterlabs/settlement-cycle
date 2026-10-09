"""Operator-approved receipts are earned cash, not new borrowed principal."""
import gzip
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from settle.domain.config import load_prime_by_id
from settle.domain.primes import Address, Chain
from settle.extract.hypersync import LogRow
from settle.normalize import allocation_capital as source


def test_historical_confirmed_receipts_are_income_without_new_debt(monkeypatch):
    with gzip.open(Path("tests/fixtures/spark_unclassified_boundary_receipts.json.gz"), "rt") as f:
        evidence = json.load(f)
    logs = [LogRow(**r) for r in evidence["rows"]]
    prime = load_prime_by_id("spark")
    prime = replace(prime, alm={Chain.ETHEREUM: prime.alm[Chain.ETHEREUM]},
                    subproxy={}, venues=[v for v in prime.venues if v.id in {"S26", "S28", "S30"}])
    monkeypatch.setattr(source.hypersync_store, "fetch_logs", lambda *a, **k: logs)
    monkeypatch.setattr(source, "get_unit_price", lambda *a, **k: Decimal(1))
    history = source.fetch_capital_history(prime, {Chain.ETHEREUM: evidence["pin"]})
    assert len(logs) == 20
    assert sum(m.external_income for b in history.batches for m in b.movements) == Decimal("10193206.19")
    assert all(b.minted == 0 and not any(b.minted_by_ilk.values()) for b in history.batches)
    assert all(not b.external_funding for b in history.batches)
    assert all(m.external_income == m.change for b in history.batches for m in b.movements)


def test_new_sources_do_not_activate_in_published_settlement_months():
    prime = load_prime_by_id("spark")
    senders = {Address.from_str(s) for s in (
        "0xd0ec8cc7414f27ce85f8dece6b4a58225f273311",
        "0x6a01c16eb312b80535f4799e4bf7522b715aacff",
        "0x1e30f9c2c688f85c82111d1d262bfd127e687282",
    )}
    assert not senders.intersection(prime.external_sources_for_period(Chain.ETHEREUM, date(2026, 9, 1)))
    assert senders.issubset(prime.external_sources_for_period(Chain.ETHEREUM, date(2026, 10, 1)))
    assert all(v.external_yield_source for v in prime.venues if v.id in {"S26", "S28", "S30"})
    assert Address.from_str("0x2e1b01adabb8d4981863394bea23a1263cbaedfc") not in prime.external_alm_sources[Chain.ETHEREUM]


@pytest.mark.parametrize("chain,venue,total", [
    (Chain.BASE, "S39", "1126433.663777"),
    (Chain.AVALANCHE_C, "S55", "76629.483846"),
])
def test_operations_income_from_actual_receipts(monkeypatch, chain, venue, total):
    with gzip.open("tests/fixtures/spark_remaining_simple_receipts.json.gz", "rt") as f:
        evidence = json.load(f)
    logs = []
    for key, entry in evidence.items():
        if not key.startswith(chain.value + ":"):
            continue
        for r in entry["receipt"]["logs"]:
            topics = (r["topics"] + [None] * 4)[:4]
            logs.append(LogRow(int(r["blockNumber"], 16), int(r["logIndex"], 16),
                               entry["batch"]["timestamp"], r["address"], *topics,
                               r["data"], r["transactionHash"]))
    prime = load_prime_by_id("spark")
    sender = Address.from_str("0x2e1b01adabb8d4981863394bea23a1263cbaedfc")
    assert sender not in prime.external_sources_for_period(chain, date(2026, 9, 1))
    assert sender in prime.external_sources_for_period(chain, date(2026, 10, 1))
    assert next(v for v in prime.venues if v.id == venue).external_yield_source
    prime = replace(prime, alm={chain: prime.alm[chain]}, subproxy={},
                    venues=[v for v in prime.venues if v.id == venue])
    monkeypatch.setattr(source.hypersync_store, "fetch_logs", lambda *a, **k: logs)
    monkeypatch.setattr(source, "get_unit_price", lambda *a, **k: Decimal(1))
    history = source.fetch_capital_history(prime, {chain: max(r.block_number for r in logs)})
    assert sum(m.external_income for b in history.batches for m in b.movements) == Decimal(total)
    assert all(m.change == m.external_income > 0 for b in history.batches for m in b.movements)
    assert all(b.minted == 0 and not any(b.minted_by_ilk.values()) and not b.external_funding
               for b in history.batches)


def test_maple_affiliation_does_not_classify_unconfirmed_receipt():
    prime = load_prime_by_id("spark")
    sender = Address.from_str("0xc8a3e1e0776b912047c89dc16470fd9c7ea1141d")
    assert sender not in prime.external_alm_sources[Chain.ETHEREUM]
