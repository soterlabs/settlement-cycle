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


def test_tracing_classifications_do_not_change_settlement_recognition():
    prime = load_prime_by_id("spark")
    for chain, senders in prime.capital_income_sources.items():
        for month in (date(2025, 1, 1), date(2026, 1, 1), date(2026, 9, 1), date(2026, 10, 1)):
            assert not set(senders).intersection(prime.external_sources_for_period(chain, month))
    assert not any(v.external_yield_source for v in prime.venues if v.id in {"S30", "S39", "S55"})


@pytest.mark.parametrize("chain,venue,total", [
    (Chain.BASE, "S39", "1126433.663777"),
    (Chain.AVALANCHE_C, "S55", "76629.483846"),
    (Chain.ETHEREUM, "S26", "383178.08"),
])
def test_confirmed_income_from_actual_receipts(monkeypatch, chain, venue, total):
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
    prime = replace(prime, alm={chain: prime.alm[chain]}, subproxy={},
                    venues=[v for v in prime.venues if v.id == venue])
    monkeypatch.setattr(source.hypersync_store, "fetch_logs", lambda *a, **k: logs)
    monkeypatch.setattr(source, "get_unit_price", lambda *a, **k: Decimal(1))
    history = source.fetch_capital_history(prime, {chain: max(r.block_number for r in logs)})
    assert sum(m.external_income for b in history.batches for m in b.movements) == Decimal(total)
    # Other unidentified Ethereum receipts in the fixture remain unclassified.
    assert all(m.change == m.external_income for b in history.batches for m in b.movements
               if m.external_income > 0)
    assert all(b.minted == 0 and not any(b.minted_by_ilk.values()) and not b.external_funding
               for b in history.batches)


def test_other_unclassified_payers_remain_unclassified():
    prime = load_prime_by_id("spark")
    for address in ("0xaa2461f0f0a3de5feaf3273eae16def861cf594e",
                    "0xcd531ae9efcce479654c4926dec5f6209531ca7b"):
        sender = Address.from_str(address)
        assert sender not in prime.capital_income_sources[Chain.ETHEREUM]
        assert sender not in prime.external_alm_sources[Chain.ETHEREUM]
