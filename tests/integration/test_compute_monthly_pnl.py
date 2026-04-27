"""Integration test: full ``compute_monthly_pnl`` orchestration with mock sources.

Verifies the wiring between Compute → Normalize → Sources without hitting
network. Uses a synthetic OBEX-like scenario sized for closed-form math.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from settle.compute import Sources, compute_monthly_pnl
from settle.compute._helpers import daily_compounding_factor
from settle.compute.agent_rate import SUBPROXY_USDS_SPREAD
from settle.compute.sky_revenue import BORROW_RATE_SPREAD
from settle.domain import Chain, Month
from settle.domain.config import load_prime

from ..fixtures.mock_sources import (
    MockBalanceSource,
    MockConvertToAssetsSource,
    MockDebtSource,
    MockPositionBalanceSource,
    MockSSRSource,
)


@pytest.fixture
def obex(config_dir: Path):
    return load_prime(config_dir / "obex.yaml")


@pytest.fixture
def fixed_pin_blocks():
    """Skip RPC by passing both pin sets explicitly."""
    return {
        "eom": {Chain.ETHEREUM: 24971074},
        "som": {Chain.ETHEREUM: 24700000},
    }


def test_monthly_pnl_zero_book_zero_pnl(obex, fixed_pin_blocks):
    """Empty timeseries / zero balances → zero PnL. Sanity gate."""
    sources = Sources(
        debt=MockDebtSource(),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({
            "effective_date": [date(2025, 12, 16)],
            "ssr_apy":        [0.04],
        })),
        position_balance=MockPositionBalanceSource(raw_balance=0),
        convert_to_assets=MockConvertToAssetsSource(raw_assets=10**6),  # pps = 1.0
    )

    result = compute_monthly_pnl(
        obex, Month(2026, 3),
        sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )

    assert result.sky_revenue == Decimal("0")
    assert result.agent_rate == Decimal("0")
    assert result.prime_agent_revenue == Decimal("0")
    assert result.monthly_pnl == Decimal("0")


def test_monthly_pnl_obex_synthetic_one_venue(obex, fixed_pin_blocks):
    """OBEX-shaped scenario, all numbers chosen for closed-form math.

    Setup (constant throughout March 2026):
      cum_debt           = 100_000_000   USDS
      subproxy_usds      =  20_000_000   USDS  (earns SSR + 0.20%)
      subproxy_susds     =          0
      alm_usds           =          0
      ssr                = 4.00%

    Position (one venue, syrupUSDC):
      balance_som  = 100M shares    pps_som = 1.04   →  value_som = 104M
      balance_eom  = 100M shares    pps_eom = 1.05   →  value_eom = 105M
      no inflows during the period

    Expected (Decimal arithmetic):
      utilized           = 100M − 20M = 80M
      borrow_apy         = 4.00% + 0.30% = 4.30%
      sky_revenue        = 31 × 80M × ((1.043)^(1/365) − 1)
      agent_rate         = 31 × 20M × ((1.042)^(1/365) − 1)
      prime_revenue      = (105M − 104M) − 0 = 1M
      monthly_pnl        = prime_revenue + agent_rate − sky_revenue
    """
    # --- inputs ---
    debt_df = pd.DataFrame({
        "block_date": [date(2025, 11, 17)],
        "daily_dart": [100_000_000.0],
        "cum_debt":   [100_000_000.0],
    })
    sub_usds_df = pd.DataFrame({
        "block_date":  [date(2025, 11, 17)],
        "daily_net":   [20_000_000.0],
        "cum_balance": [20_000_000.0],
    })
    empty_balance_df = pd.DataFrame({
        "block_date": [], "daily_net": [], "cum_balance": [],
    })
    ssr_df = pd.DataFrame({
        "effective_date": [date(2025, 12, 16)],
        "ssr_apy":        [0.04],
    })

    # MockBalanceSource serves both subproxy USDS, subproxy sUSDS, ALM USDS,
    # and the directed venue inflow. We need to dispatch by holder/from_addr.
    class _SmartBalances(MockBalanceSource):
        def cumulative_balance_timeseries(
            self, chain, token, holder, start, pin_block,
        ):
            # OBEX subproxy USDS holdings — non-empty for our subproxy address only.
            self.cumulative_calls.append((chain, token, holder, start, pin_block))
            if holder == obex.subproxy[Chain.ETHEREUM].value and token == bytes.fromhex(
                "dc035d45d973e3ec169d2276ddab16f1e407384f"
            ):
                return sub_usds_df
            return empty_balance_df

        def directed_inflow_timeseries(
            self, chain, token, from_addr, to_addr, start, pin_block,
        ):
            self.directed_calls.append((chain, token, from_addr, to_addr, start, pin_block))
            return pd.DataFrame({
                "block_date": [], "daily_inflow": [], "cum_inflow": [],
            })

    # Position balance source — return 100M shares (raw = 100M × 10^6).
    position_balance_src = MockPositionBalanceSource(raw_balance=100_000_000 * 10**6)

    # ConvertToAssets needs to differentiate SoM (pps = 1.04) vs EoM (pps = 1.05).
    class _PriceByBlock(MockConvertToAssetsSource):
        def convert_to_assets(self, chain, vault, shares, block):
            self.calls.append((chain, vault, shares, block))
            if block == fixed_pin_blocks["som"][Chain.ETHEREUM]:
                return int(Decimal("1.04") * 10**6)
            return int(Decimal("1.05") * 10**6)

    sources = Sources(
        debt=MockDebtSource(debt_df),
        balance=_SmartBalances(),
        ssr=MockSSRSource(ssr_df),
        position_balance=position_balance_src,
        convert_to_assets=_PriceByBlock(),
    )

    # --- act ---
    result = compute_monthly_pnl(
        obex, Month(2026, 3),
        sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )

    # --- assert ---
    days = 31
    sky_factor = daily_compounding_factor(Decimal("0.04") + BORROW_RATE_SPREAD)
    agent_factor = daily_compounding_factor(Decimal("0.04") + SUBPROXY_USDS_SPREAD)
    expected_sky = Decimal("80000000") * days * sky_factor
    expected_agent = Decimal("20000000") * days * agent_factor

    assert result.sky_revenue == expected_sky
    assert result.agent_rate == expected_agent
    assert result.prime_agent_revenue == Decimal("1000000")
    assert result.monthly_pnl == expected_agent + Decimal("1000000") - expected_sky

    # Per-venue breakdown
    assert len(result.venue_breakdown) == 1
    v = result.venue_breakdown[0]
    assert v.venue_id == "V1"
    assert v.value_som == Decimal("104000000")
    assert v.value_eom == Decimal("105000000")
    assert v.period_inflow == Decimal("0")
    assert v.revenue == Decimal("1000000")

    # Provenance — both pin sets recorded.
    assert result.pin_blocks_som == fixed_pin_blocks["som"]
    assert result.period.pin_blocks == fixed_pin_blocks["eom"]


def test_monthly_pnl_invariant_holds(obex, fixed_pin_blocks):
    """The MonthlyPnL ``__post_init__`` invariant gates round-trip math."""
    sources = Sources(
        debt=MockDebtSource(),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({"effective_date": [date(2025, 12, 16)], "ssr_apy": [0.04]})),
        position_balance=MockPositionBalanceSource(raw_balance=0),
        convert_to_assets=MockConvertToAssetsSource(raw_assets=10**6),
    )
    result = compute_monthly_pnl(
        obex, Month(2026, 3),
        sources=sources,
        pin_blocks_eom=fixed_pin_blocks["eom"],
        pin_blocks_som=fixed_pin_blocks["som"],
    )
    assert result.monthly_pnl == (
        result.prime_agent_revenue + result.agent_rate - result.sky_revenue
    )


def test_monthly_pnl_invokes_block_resolver_for_both_som_and_eom(obex):
    """When pin_blocks_eom/som are not supplied, `compute_monthly_pnl` must
    delegate to the configured `IBlockResolver` exactly twice per chain (one
    SoM anchor, one EoM anchor) and the SoM anchor must precede the EoM anchor
    by ~1 month."""
    from datetime import datetime, time, timedelta, timezone

    from ..fixtures.mock_sources import MockBlockResolver

    resolver = MockBlockResolver(default=99)  # ALM uses this for any unknown anchor
    sources = Sources(
        debt=MockDebtSource(),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({"effective_date": [date(2025, 1, 1)], "ssr_apy": [0.04]})),
        position_balance=MockPositionBalanceSource(raw_balance=0),
        convert_to_assets=MockConvertToAssetsSource(raw_assets=10**6),
        block_resolver=resolver,
    )

    result = compute_monthly_pnl(
        obex, Month(2026, 3), sources=sources,
        # Both pin sets None → resolver must be invoked
    )

    # Resolver called exactly twice for ethereum (OBEX has only one chain).
    chains_seen = [c for c, _ in resolver.calls]
    assert chains_seen == ["ethereum", "ethereum"]
    anchors = [a for _, a in resolver.calls]
    eom_anchor = datetime.combine(date(2026, 3, 31), time.max, tzinfo=timezone.utc)
    som_anchor = datetime.combine(date(2026, 2, 28), time.max, tzinfo=timezone.utc)
    assert eom_anchor in anchors
    assert som_anchor in anchors
    # The pin blocks ended up on the result.
    assert result.period.pin_blocks[Chain.ETHEREUM] == 99
    assert result.pin_blocks_som[Chain.ETHEREUM] == 99
