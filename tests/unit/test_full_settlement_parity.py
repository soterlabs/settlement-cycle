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


def test_raw_dune_oracle_preserves_low_wad_digits():
    raw = "2943862416409310890937532951"
    frame = pd.DataFrame([{"block_date": "2026-08-31", "raw": raw}])
    out = mod.normalize_raw(frame, {"raw": "cum_debt"}, 18)
    assert out.cum_debt.iloc[0] == Decimal("2943862416.409310890937532951")
    frame.loc[0, "raw"] = "1.5"
    with pytest.raises(ValueError, match="non-integer"):
        mod.normalize_raw(frame, {"raw": "cum_debt"}, 18)


def test_raw_shared_oracle_keeps_other_venue_queries(monkeypatch):
    from settle.domain.config import load_prime_by_id

    prime = load_prime_by_id("spark")
    source = mod.RawDuneSharedBalanceOracle(prime)
    calls = []

    def query(path, params=None, pin_block=None):
        calls.append(path.name)
        if "raw_parity" in path.name:
            return pd.DataFrame([{"block_date": "2026-08-31", "daily_net": "90000000000000000000000000", "cum_balance": "90000000000000000000000000"}])
        return pd.DataFrame()

    monkeypatch.setattr(mod.dune, "execute_query", query)
    from settle.normalize.sources import dune_balances
    monkeypatch.setattr(dune_balances, "execute_query", query)
    holder = prime.alm[Chain.ETHEREUM].value
    out = source.cumulative_balance_timeseries("ethereum", mod.USDS_ETHEREUM.address.value, holder, prime.start_date, 123)
    assert out.cum_balance.iloc[0] == Decimal(90000000)
    source.cumulative_balance_timeseries("ethereum", bytes(20), holder, prime.start_date, 123)
    assert calls == ["transfer_timeseries_raw_parity.sql", "transfer_timeseries.sql"]


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
