"""Curve daily series must carry forward on transient RPC failure, not zero.

A failed day recorded as $0 removes that day's utilized exclusion (the
prime is over-charged BR) and, for capped SDE entries, pollutes the
Σcum/Σuncapped sd_share ratio. The lending sibling
(``_aggregate_lending_idle_usds``) was hardened for exactly this; these
tests pin the same semantics on the two Curve paths:

* transient transport error mid-month → previous day's value carried;
* transport error before ANY successful read → raise (never seed the
  month with zeros);
* non-transport errors propagate (no silent carry on programming bugs).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from settle.compute.monthly_pnl import (
    _aggregate_curve_idle_usds,
    _curve_sde_asset_value_timeseries,
)
from settle.domain.period import Period
from settle.domain.primes import Address, Chain
from settle.extract.rpc import RPCError

USDT = Address.from_str("0xdac17f958d2ee523a2206206994597c13d831ec7")
POOL = Address.from_str("0x00000000000000000000000000000000000000aa")
ALM = Address.from_str("0x00000000000000000000000000000000000000bb")

_PERIOD = Period(
    start=date(2026, 3, 1), end=date(2026, 3, 3),
    pin_blocks={Chain.ETHEREUM: 1},
)


def _venue():
    return SimpleNamespace(
        id="S24",
        chain=Chain.ETHEREUM,
        token=SimpleNamespace(address=POOL, decimals=18),
        holder_override=None,
        curve_idle_usds=SimpleNamespace(coin=USDT, sky_savings_token=False),
    )


def _prime(venue):
    return SimpleNamespace(
        id="spark", alm={Chain.ETHEREUM: ALM}, venues=[venue],
    )


class _Resolver:
    def block_at_or_before(self, chain, anchor):
        return anchor.day  # block == day-of-month, deterministic


class _FlakyPool:
    """Healthy pool state, except raises RPCError on the given days."""

    def __init__(self, fail_days: set[int]):
        self.fail_days = fail_days

    def read_pool(self, chain, pool, block):
        if block in self.fail_days:
            raise RPCError(f"transient failure at block {block}")
        return SimpleNamespace(
            total_supply=200 * 10**18,
            coins=[USDT],
            balances=[100 * 10**6],  # $100 USDT reserve
        )


@pytest.fixture(autouse=True)
def _alm_holds_half_the_pool(monkeypatch):
    from settle.extract import rpc as _rpc
    # ALM LP balance = 100e18 of 200e18 total → 50% share → $50/day.
    monkeypatch.setattr(
        _rpc, "balance_of", lambda chain, token, holder, block: 100 * 10**18,
    )


def test_curve_sde_carries_forward_on_transient_failure():
    df = _curve_sde_asset_value_timeseries(
        _prime(_venue()), _venue(), _PERIOD,
        sde_coin=USDT,
        curve_pool_source=_FlakyPool(fail_days={2}),
        block_resolver=_Resolver(),
    )
    values = list(df["cum_value"])
    assert values[0] == Decimal("50")
    assert values[1] == Decimal("50"), "failed day must carry forward, not $0"
    assert values[2] == Decimal("50")


def test_curve_sde_day_one_failure_raises():
    with pytest.raises(RPCError):
        _curve_sde_asset_value_timeseries(
            _prime(_venue()), _venue(), _PERIOD,
            sde_coin=USDT,
            curve_pool_source=_FlakyPool(fail_days={1, 2, 3}),
            block_resolver=_Resolver(),
        )


def test_curve_sde_programming_error_propagates():
    class _Buggy:
        def read_pool(self, chain, pool, block):
            raise KeyError("not a transport error")

    with pytest.raises(KeyError):
        _curve_sde_asset_value_timeseries(
            _prime(_venue()), _venue(), _PERIOD,
            sde_coin=USDT,
            curve_pool_source=_Buggy(),
            block_resolver=_Resolver(),
        )


def test_curve_idle_carries_forward_on_transient_failure():
    venue = _venue()
    df, spread = _aggregate_curve_idle_usds(
        _prime(venue), _PERIOD,
        curve_pool_source=_FlakyPool(fail_days={2}),
        block_resolver=_Resolver(),
    )
    assert spread == Decimal("0")
    values = list(df["cum_balance"])
    assert values[0] == Decimal("50")
    assert values[1] == Decimal("50"), "failed day must carry forward, not $0"
    assert values[2] == Decimal("50")


def test_curve_idle_day_one_failure_raises():
    venue = _venue()
    with pytest.raises(RPCError):
        _aggregate_curve_idle_usds(
            _prime(venue), _PERIOD,
            curve_pool_source=_FlakyPool(fail_days={1, 2, 3}),
            block_resolver=_Resolver(),
        )
