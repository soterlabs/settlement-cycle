"""Unit tests for `settle.normalize.balances` using mock sources."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from settle.domain import Address, Chain, Month, Period
from settle.domain.config import load_prime
from settle.normalize.balances import (
    get_alm_balance_timeseries,
    get_subproxy_balance_timeseries,
    get_venue_inflow_timeseries,
)

from ..fixtures.mock_sources import MockBalanceSource


def _obex(config_dir: Path):
    return load_prime(config_dir / "obex.yaml")


def _period() -> Period:
    return Period.from_month(Month(2026, 4), pin_blocks={Chain.ETHEREUM: 24971074})


def test_subproxy_balance_uses_prime_subproxy_address(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]                    # syrupUSDC
    underlying = venue.underlying             # USDC
    assert underlying is not None

    src = MockBalanceSource(
        cumulative_df=pd.DataFrame({
            "block_date": [date(2025, 11, 17)],
            "daily_net": [21_000_000.0],
            "cum_balance": [21_000_000.0],
        }),
    )

    df = get_subproxy_balance_timeseries(obex, Chain.ETHEREUM, underlying, _period(), source=src)
    assert df.iloc[0].cum_balance == 21_000_000.0

    chain, token, holder, start, pin, _min_transfer = src.cumulative_calls[0]
    assert chain == "ethereum"
    assert holder == obex.subproxy[Chain.ETHEREUM].value
    assert token == underlying.address.value
    assert start == obex.start_date
    assert pin == 24971074


def test_alm_balance_uses_prime_alm_address(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]
    src = MockBalanceSource()

    get_alm_balance_timeseries(obex, Chain.ETHEREUM, venue.token, _period(), source=src)

    chain, token, holder, _, _, _ = src.cumulative_calls[0]
    assert holder == obex.alm[Chain.ETHEREUM].value
    assert token == venue.token.address.value


def test_subproxy_balance_rejects_chain_not_configured(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]
    underlying = venue.underlying
    assert underlying is not None

    period = Period.from_month(Month(2026, 4), pin_blocks={Chain.BASE: 999})
    src = MockBalanceSource()
    with pytest.raises(ValueError, match="no subproxy on base"):
        get_subproxy_balance_timeseries(obex, Chain.BASE, underlying, period, source=src)


def test_venue_inflow_directs_alm_to_venue(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]                    # syrupUSDC
    underlying = venue.underlying             # USDC
    assert underlying is not None

    src = MockBalanceSource(
        directed_df=pd.DataFrame({
            "block_date": [date(2025, 11, 18)],
            "daily_inflow": [50_000_000.0],
            "cum_inflow": [50_000_000.0],
        }),
    )

    df = get_venue_inflow_timeseries(
        obex, Chain.ETHEREUM, underlying, venue.token.address, _period(), source=src,
    )
    assert df.iloc[0].cum_inflow == 50_000_000.0

    chain, token, from_addr, to_addr, start, pin = src.directed_calls[0]
    assert chain == "ethereum"
    assert from_addr == obex.alm[Chain.ETHEREUM].value
    assert to_addr == venue.token.address.value
    assert token == underlying.address.value
    assert start == obex.start_date
    assert pin == 24971074


def test_period_pin_block_required_for_chain(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]
    underlying = venue.underlying
    assert underlying is not None

    # Period has no pin_block at all
    period = Period.from_month(Month(2026, 4), pin_blocks={})
    src = MockBalanceSource()
    with pytest.raises(ValueError, match="missing pin_block for chain ethereum"):
        get_subproxy_balance_timeseries(obex, Chain.ETHEREUM, underlying, period, source=src)
