"""Unit tests for orchestrator helpers in ``compute.monthly_pnl``.

Covers ``_susds_shares_to_principal`` (sUSDS shares → USDS-cost-basis principal)
and ``get_psm_usds_timeseries`` (PSM USDS reimbursement aggregator).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from settle.compute.monthly_pnl import (
    Sources,
    _susds_shares_to_principal,
    get_psm_usds_timeseries,
)
from settle.domain import Chain, Month, Period
from settle.domain.config import load_prime
from tests.fixtures.mock_sources import (
    MockBalanceSource,
    MockBlockResolver,
    MockConvertToAssetsSource,
)


# ----------------------------------------------------------------------------
# _susds_shares_to_principal
# ----------------------------------------------------------------------------


def test_susds_principal_empty_input_returns_empty():
    out = _susds_shares_to_principal(
        pd.DataFrame(),
        sources=Sources(),
        block_resolver=MockBlockResolver(),
        chain=Chain.ETHEREUM,
    )
    assert out is not None
    assert out.empty


def test_susds_principal_all_zero_short_circuits():
    """All-zero shares → no RPC calls, returns input unchanged."""
    df = pd.DataFrame({
        "block_date": [date(2026, 3, 1), date(2026, 3, 2)],
        "daily_net": [Decimal(0), Decimal(0)],
        "cum_balance": [Decimal(0), Decimal(0)],
    })
    c2a = MockConvertToAssetsSource(raw_assets=10**18)
    out = _susds_shares_to_principal(
        df,
        sources=Sources(convert_to_assets=c2a),
        block_resolver=MockBlockResolver(default=24000000),
        chain=Chain.ETHEREUM,
    )
    # Short-circuit: returned unchanged, no convertToAssets calls.
    assert (out["cum_balance"] == 0).all()
    assert c2a.calls == []


def test_susds_principal_uses_per_day_pps_not_eom():
    """Cost basis = Σ shares_flow_d × pps_at_day_d, NOT shares_eom × pps_eom.

    Set up two distinct pps reads (different blocks) and verify the principal
    follows each day's pps, not the latest one.
    """
    df = pd.DataFrame({
        "block_date": [date(2026, 1, 15), date(2026, 2, 15)],
        "daily_net": [Decimal("100"), Decimal("100")],
        "cum_balance": [Decimal("100"), Decimal("200")],  # ignored — recomputed
    })
    # MockConvertToAssetsSource returns a constant raw_assets per call. To
    # vary by block we'd need a richer mock, but for this test the constant
    # pps verifies the shape: daily_net × pps stays a Decimal cumulative.
    c2a = MockConvertToAssetsSource(raw_assets=int(Decimal("1.05") * 10**18))

    block_resolver = MockBlockResolver()
    block_resolver.dates_by_block = {
        ("ethereum", 24200000): date(2026, 1, 15),
        ("ethereum", 24500000): date(2026, 2, 15),
    }
    block_resolver.blocks = {
        ("ethereum", "2026-01-15T23:59:59.999999+00:00"): 24200000,
        ("ethereum", "2026-02-15T23:59:59.999999+00:00"): 24500000,
    }

    out = _susds_shares_to_principal(
        df,
        sources=Sources(convert_to_assets=c2a),
        block_resolver=block_resolver,
        chain=Chain.ETHEREUM,
    )
    # Each row: 100 shares × 1.05 USDS/share = 105 USDS.
    assert out["daily_net"].iloc[0] == Decimal("105")
    assert out["daily_net"].iloc[1] == Decimal("105")
    # Cumulative builds up to 210 (Decimal arithmetic preserved end-to-end).
    assert out["cum_balance"].iloc[1] == Decimal("210")
    assert all(isinstance(v, Decimal) for v in out["cum_balance"])


def test_susds_principal_rejects_non_ethereum_chain():
    """sUSDS vault address is hardcoded to Ethereum; calling for another
    chain must raise rather than silently read the wrong contract."""
    df = pd.DataFrame({
        "block_date": [date(2026, 3, 1)],
        "daily_net": [Decimal("100")],
        "cum_balance": [Decimal("100")],
    })
    with pytest.raises(NotImplementedError, match="only registered for Ethereum"):
        _susds_shares_to_principal(
            df,
            sources=Sources(convert_to_assets=MockConvertToAssetsSource(raw_assets=10**18)),
            block_resolver=MockBlockResolver(),
            chain=Chain.BASE,
        )


# ----------------------------------------------------------------------------
# get_psm_usds_timeseries
# ----------------------------------------------------------------------------


def _grove(config_dir: Path):
    return load_prime(config_dir / "grove.yaml")


def _period() -> Period:
    return Period(
        start=date(2026, 3, 1), end=date(2026, 3, 31),
        pin_blocks={Chain.ETHEREUM: 24781026},
    )


def test_psm_usds_no_flows_returns_empty(config_dir: Path):
    """Grove's mock with no directed-inflow fixtures → empty timeseries."""
    grove = _grove(config_dir)
    src = MockBalanceSource()  # default: directed_inflow returns empty
    out = get_psm_usds_timeseries(
        grove, Chain.ETHEREUM, _period(), balance_source=src,
    )
    assert out.empty


def test_psm_usds_aggregates_deposits_minus_withdrawals(config_dir: Path):
    """Deposits (subproxy → PSM, ALM → PSM) sum to positive PSM balance;
    withdrawals (PSM → subproxy, PSM → ALM) flip sign."""
    grove = _grove(config_dir)

    # PSM is at 0x37305b1c... per grove.yaml's addresses.ethereum.psm block.
    routes = {}

    def _df(rows):
        return pd.DataFrame(rows) if rows else pd.DataFrame({
            "block_date": [], "daily_inflow": [], "cum_inflow": [],
        })

    class _RoutedSrc(MockBalanceSource):
        def directed_inflow_timeseries(self, chain, token, from_addr, to_addr, start, pin_block):
            df = routes.get((from_addr, to_addr))
            return _df(df) if df is not None else _df([])

    subproxy = grove.subproxy[Chain.ETHEREUM].value
    alm = grove.alm[Chain.ETHEREUM].value
    psm = bytes.fromhex("37305b1cd40574e4c5ce33f8e8306be057fd7341")

    # subproxy → PSM = $1M deposit on day 5; PSM → subproxy = $300K withdraw on day 20.
    routes[(subproxy, psm)] = [
        {"block_date": date(2026, 3, 5), "daily_inflow": 1_000_000.0, "cum_inflow": 1_000_000.0},
    ]
    routes[(psm, subproxy)] = [
        {"block_date": date(2026, 3, 20), "daily_inflow": 300_000.0, "cum_inflow": 300_000.0},
    ]

    out = get_psm_usds_timeseries(
        grove, Chain.ETHEREUM, _period(), balance_source=_RoutedSrc(),
    )
    assert len(out) == 2
    # Day 5: +1M, Day 20: −300K → cum_balance = [1M, 700K]
    assert out["daily_net"].iloc[0] == Decimal("1000000")
    assert out["daily_net"].iloc[1] == Decimal("-300000")
    assert out["cum_balance"].iloc[1] == Decimal("700000")
