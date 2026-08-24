"""agent_rate_start_date gating + multi-ilk debt summation (Diamond PAU)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from settle.compute.agent_rate import compute_agent_rate
from settle.domain.period import Period


def _period_july():
    return Period(start=date(2026, 7, 1), end=date(2026, 7, 31), pin_blocks={})


def _balances(amount: str, since: str):
    return pd.DataFrame({
        "block_date": [date.fromisoformat(since)],
        "daily_net": [Decimal(amount)],
        "cum_balance": [Decimal(amount)],
    })


def _flat_ssr(apy: str = "0.0352"):
    return pd.DataFrame({
        "effective_date": [date(2024, 9, 1)],
        "ssr_apy": [Decimal(apy)],
    })


def test_agent_rate_start_date_gates_accrual():
    usds = _balances("10000000", "2026-03-30")   # Osero: seeded pre-allocation
    susds = pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})
    full = compute_agent_rate(_period_july(), usds, susds, _flat_ssr())
    gated = compute_agent_rate(
        _period_july(), usds, susds, _flat_ssr(), start_date=date(2026, 7, 20),
    )
    # 12 accruing days (Jul 20–31) out of 31. The accrual COMPOUNDS
    # (2026-08-24), so the gated figure is NOT the linear 12/31 of the full
    # month — it is P × ((1+f)^12 − 1) against the full month's
    # P × ((1+f)^31 − 1).
    from settle.compute._helpers import combine_apys, daily_compounding_factor
    from settle.compute.agent_rate import AGENT_RATE_OVER_SSR
    f = daily_compounding_factor(combine_apys(Decimal("0.0352"), AGENT_RATE_OVER_SSR))
    P = Decimal("10000000")
    assert abs(gated - P * ((1 + f) ** 12 - 1)) < Decimal("1e-9")
    assert abs(full - P * ((1 + f) ** 31 - 1)) < Decimal("1e-9")
    assert gated < full / 31 * 12   # compounding is back-loaded
    # No gate (default) == full month.
    assert compute_agent_rate(_period_july(), usds, susds, _flat_ssr(),
                              start_date=None) == full


def test_agent_rate_start_after_period_is_zero():
    usds = _balances("10000000", "2026-03-30")
    susds = pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})
    assert compute_agent_rate(
        _period_july(), usds, susds, _flat_ssr(), start_date=date(2026, 8, 1),
    ) == Decimal("0")


def test_mock_debt_source_dispatches_by_ilk():
    from tests.fixtures.mock_sources import MockDebtSource

    bloom = b"ALLOCATOR-BLOOM-A".ljust(32, b"\x00")
    grove_a = b"ALLOCATOR-GROVE-A".ljust(32, b"\x00")
    other = b"ALLOCATOR-OTHER-A".ljust(32, b"\x00")
    bloom_df = pd.DataFrame({"block_date": [date(2026, 7, 1)],
                             "daily_dart": [Decimal(5)], "cum_debt": [Decimal(5)]})
    grove_df = pd.DataFrame({"block_date": [date(2026, 7, 21)],
                             "daily_dart": [Decimal(1)], "cum_debt": [Decimal(1)]})
    src = MockDebtSource(bloom_df, df_by_ilk={bloom: bloom_df, grove_a: grove_df})
    assert src.debt_timeseries(bloom, date(2026, 7, 1), 1)["cum_debt"].iloc[0] == 5
    assert src.debt_timeseries(grove_a, date(2026, 7, 1), 1)["cum_debt"].iloc[0] == 1
    # Unknown ilk with a non-empty map → EMPTY (never the default frame):
    # a missing fixture must not double-count the primary ilk's debt.
    assert src.debt_timeseries(other, date(2026, 7, 1), 1).empty
    # Legacy single-ilk behaviour: empty map → default frame for any ilk.
    legacy = MockDebtSource(bloom_df)
    assert legacy.debt_timeseries(other, date(2026, 7, 1), 1)["cum_debt"].iloc[0] == 5


def test_debt_fallback_merges_extra_ilks():
    """The no-resolver fallback must include extra_ilks Art, not drop it
    (PR-164 review finding: extra series were fetched then discarded)."""
    from settle.domain.config import load_prime
    from settle.domain.period import Period as _P
    from settle.domain import Chain
    from settle.normalize.debt import get_debt_timeseries
    from tests.fixtures.mock_sources import MockDebtSource
    from pathlib import Path

    grove = load_prime(Path("config/grove.yaml"))
    assert grove.extra_ilks  # ALLOCATOR-GROVE-A
    bloom = grove.ilk_bytes32
    grove_a = grove.extra_ilks[0]
    src = MockDebtSource(df_by_ilk={
        bloom: pd.DataFrame({"block_date": [date(2026, 7, 1)],
                             "daily_dart": [Decimal(2_500_000_000)],
                             "cum_debt": [Decimal(2_500_000_000)]}),
        grove_a: pd.DataFrame({"block_date": [date(2026, 7, 21)],
                               "daily_dart": [Decimal(1_000_000)],
                               "cum_debt": [Decimal(1_000_000)]}),
    })
    period = _P(start=date(2026, 7, 1), end=date(2026, 7, 31),
                pin_blocks={Chain.ETHEREUM: 25656292})
    df = get_debt_timeseries(grove, period, source=src, block_resolver=None)
    assert df["cum_debt"].iloc[0] == Decimal(2_500_000_000)          # Jul 1: bloom only
    assert df["cum_debt"].iloc[-1] == Decimal(2_501_000_000)         # Jul 21+: summed


def test_subsidy_loader_rejects_typo_column(tmp_path):
    """A rates row with NO known rate column is a typo, not a sparse series —
    must raise instead of silently dropping the day into carry-forward."""
    import pytest as _pytest
    from settle.domain.subsidy import load_reference_rates

    p = tmp_path / "rates.yaml"
    p.write_text(
        "rates:\n"
        "  - effective_date: '2026-07-22'\n"
        "    tbill_3m_apy: 0.0389\n"
        "  - effective_date: '2026-07-23'\n"
        "    tbil_3m_apy: 0.0395\n"     # typo'd column
    )
    with _pytest.raises(ValueError, match="none of the known rate columns"):
        load_reference_rates("tbill_3m", config_path=p)


def test_erc20_balances_multi_cutoff_single_fetch():
    """One fetch serves every cutoff; per-cutoff sums honour block bounds."""
    from settle.normalize.sources.hypersync_position_balance import (
        erc20_balances_from_transfers,
    )

    holder = bytes.fromhex("de770c84fe66e063336b31737cfe9790f18c4087")
    token = bytes.fromhex("5fc5360d0400a0fd4f2af552add042d716f1d168")
    ht = "0x" + "00" * 12 + holder.hex()
    other = "0x" + "00" * 12 + "ab" * 20

    class Row:
        def __init__(self, bn, li, t1, t2, data):
            self.block_number, self.log_index = bn, li
            self.topic1, self.topic2, self.data = t1, t2, data

    calls = []
    def fetch(chain, sel, frm, to):
        calls.append((frm, to))
        return [Row(5, 0, other, ht, hex(100)), Row(20, 0, ht, other, hex(30))]

    out = erc20_balances_from_transfers("robinhood", token, holder, (10, 25),
                                        fetch_logs=fetch)
    assert calls == [(0, 25)]           # exactly ONE fetch, to max(blocks)
    assert out == {10: 100, 25: 70}     # block-10 cutoff excludes the outflow
