from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import compare_hypersync_settlement as mod
from settle.domain.period import Month
from settle.domain.primes import Address, Chain
from settle.domain.sky_tokens import PSM3_LEG_TOKENS


def test_daily_psm_oracle_carries_quiet_days_but_rejects_intraday(monkeypatch):
    chain = Chain.BASE
    contract = Address(bytes.fromhex("11" * 20))
    prime = SimpleNamespace(psm={chain: SimpleNamespace(address=contract)}, alm={chain: contract})
    monkeypatch.setattr(mod.DunePsm3Source, "_load_holder_history", lambda *a, **k: [])
    monkeypatch.setattr(mod.DunePsm3Source, "_load_pool_history", lambda *a, **k: [])
    rows = [{"token": token.address.value, "block_date": day, "cum_balance_raw": amount}
            for token in PSM3_LEG_TOKENS[chain].values()
            for day, amount in [(date(2026, 7, 30), "100"), (date(2026, 8, 2), "120")]]
    monkeypatch.setattr(mod.dune, "execute_query", lambda *a: pd.DataFrame(rows))
    resolver = SimpleNamespace(block_at_or_before=lambda chain, anchor: anchor.date().toordinal() * 10)
    source = mod.DailyDunePsm3Oracle(prime, Month(2026, 8), resolver)
    token = PSM3_LEG_TOKENS[chain]["USDC"].address.value
    for day, expected in [(date(2026, 7, 31), 100), (date(2026, 8, 1), 100), (date(2026, 8, 3), 120)]:
        assert source.pool_reserve_at(chain.value, token, contract.value, day.toordinal() * 10, decimals=6) == expected
    with pytest.raises(AssertionError, match="outside certified EOD"):
        source.pool_reserve_at(chain.value, token, contract.value, date(2026, 8, 3).toordinal() * 10 - 1, decimals=6)
    rows.append(rows[0])
    with pytest.raises(ValueError, match="Duplicate"):
        mod.DailyDunePsm3Oracle(prime, Month(2026, 8), resolver)


@dataclass
class Result:
    sky_revenue: Decimal = Decimal(1)
    agent_rate: Decimal = Decimal(2)
    prime_agent_revenue: Decimal = Decimal(3)
    monthly_pnl: Decimal = Decimal(4)


@pytest.mark.parametrize("swallowed_dune_call", [False, True])
def test_spark_baseline_injects_dune_psm_and_candidate_forbids_fallback(monkeypatch, swallowed_dune_call):
    oracle = SimpleNamespace(pins={"base": 123})
    monkeypatch.setattr(mod, "DailyDunePsm3Oracle", lambda *a: oracle)
    calls = []

    def compute(prime, month, sources=None):
        calls.append(prime)
        if len(calls) == 1:
            assert all(p.event_source == "dune" for p in prime.psm.values())
            assert all(v.event_source == "dune" for v in prime.venues)
            assert sources.psm3 is oracle
            assert isinstance(sources.debt, mod.DuneDebtSource)
        else:
            assert all(p.event_source == "hypersync" for p in prime.psm.values())
            assert sources is None
            if swallowed_dune_call:
                try:
                    mod.dune.execute_query("must never run", {}, 123)
                except AssertionError:
                    pass
        return Result()

    monkeypatch.setattr(mod, "compute_monthly_pnl", compute)
    if swallowed_dune_call:
        with pytest.raises(AssertionError, match="attempted 1 Dune queries"):
            mod.compare("spark", "2026-08")
    else:
        report = mod.compare("spark", "2026-08")
        assert report["matched"] and report["candidate_dune_calls"] == 0
        assert report["output_sha256"]["dune"] == report["output_sha256"]["hypersync"]
