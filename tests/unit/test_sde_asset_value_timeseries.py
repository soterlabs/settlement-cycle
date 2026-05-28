"""Unit tests for ``_sde_asset_value_timeseries`` — specifically the
in-flight redemption window where ``burn_date`` is set.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from settle.compute.monthly_pnl import _sde_asset_value_timeseries
from settle.domain import Address, Chain, Period, PricingCategory, Token, Venue
from settle.domain.primes import Prime


def _venue() -> Venue:
    return Venue(
        id="E8",
        chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, Address.from_str("0x" + "aa" * 20), "JAAA", 6),
        pricing_category=PricingCategory.RWA_TRANCHE,
        underlying=Token(Chain.ETHEREUM, Address.from_str("0x" + "bb" * 20), "USDC", 6),
        label="JAAA (test)",
    )


def _prime() -> Prime:
    return Prime(
        id="grove",
        ilk_bytes32=bytes.fromhex("aa" * 32),
        start_date=date(2025, 10, 1),
        alm={Chain.ETHEREUM: Address.from_str("0x" + "cc" * 20)},
    )


def _period(start: date, end: date) -> Period:
    return Period(start=start, end=end, pin_blocks={Chain.ETHEREUM: 1})


class _ConstBalanceSource:
    """Returns one cumulative-balance row at ``start`` so every period day
    snapshots the same balance."""

    def __init__(self, balance: Decimal):
        self._balance = balance

    def cumulative_balance_timeseries(
        self, *, chain: str, token: bytes, holder: bytes, start: date,
        pin_block: int, min_transfer_amount: Decimal = Decimal(0),
    ) -> pd.DataFrame:
        return pd.DataFrame({
            "block_date":  [start],
            "daily_net":   [self._balance],
            "cum_balance": [self._balance],
        })


class _StepBalanceSource:
    """Cumulative-balance series that steps down on ``drop_date`` to
    simulate an on-chain tranche burn."""

    def __init__(self, pre: Decimal, post: Decimal, drop_date: date):
        self._pre = pre
        self._post = post
        self._drop = drop_date

    def cumulative_balance_timeseries(
        self, *, chain: str, token: bytes, holder: bytes, start: date,
        pin_block: int, min_transfer_amount: Decimal = Decimal(0),
    ) -> pd.DataFrame:
        return pd.DataFrame({
            "block_date":  [start, self._drop],
            "daily_net":   [self._pre, self._post - self._pre],
            "cum_balance": [self._pre, self._post],
        })


class _StaticBlockResolver:
    def block_at_or_before(self, chain: str, anchor_utc) -> int:
        return 1


def _const_nav(_block: int) -> Decimal:
    return Decimal("1")


def test_capped_sde_in_flight_window_keeps_cap_coverage():
    """Reproduces the Grove E8 Mar 2026 bug: cap = $325M, on-chain value
    drops from $325M to $128M on the burn date, then SDE end_date is 3
    days later. Before the fix, cum_value followed the on-chain drop;
    after the fix, cum_value stays at the cap through end_date."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    cap = Decimal("325_000_000")
    pre_burn = Decimal("454_000_000")
    post_burn = Decimal("128_000_000")
    burn = date(2026, 3, 9)
    sde_end = date(2026, 3, 12)

    bs = _StepBalanceSource(pre=pre_burn, post=post_burn, drop_date=burn)
    df = _sde_asset_value_timeseries(
        _prime(), _venue(), period,
        balance_source=bs,
        block_resolver=_StaticBlockResolver(),
        nav_at_block=_const_nav,
        cap_usd=cap,
        burn_date=burn,
        end_date=sde_end,
    )

    by_date = {r["block_date"]: r for _, r in df.iterrows()}

    # Pre-burn: capped at cap (uncapped value > cap).
    assert by_date[date(2026, 3, 8)]["cum_value"]      == cap
    assert by_date[date(2026, 3, 8)]["uncapped_value"] == pre_burn

    # Burn day → end_date: cum_value stays at cap (in-flight coverage)
    # even though uncapped_value has collapsed to the on-chain residual.
    for d in (date(2026, 3, 9), date(2026, 3, 10),
              date(2026, 3, 11), date(2026, 3, 12)):
        assert by_date[d]["cum_value"]      == cap, f"in-flight day {d}"
        assert by_date[d]["uncapped_value"] == post_burn, f"in-flight day {d}"

    # Post end_date: cum_value tracks the on-chain (uncapped, below cap).
    for d in (date(2026, 3, 13), date(2026, 3, 14), date(2026, 3, 31)):
        assert by_date[d]["cum_value"]      == post_burn, f"post-end {d}"
        assert by_date[d]["uncapped_value"] == post_burn, f"post-end {d}"


def test_capped_sde_without_burn_date_uses_on_chain_value():
    """Sanity: when burn_date is not set the behaviour is unchanged —
    cum_value is just min(on_chain_value, cap_usd) every day."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    cap = Decimal("325_000_000")
    pre = Decimal("454_000_000")
    post = Decimal("128_000_000")

    bs = _StepBalanceSource(pre=pre, post=post, drop_date=date(2026, 3, 9))
    df = _sde_asset_value_timeseries(
        _prime(), _venue(), period,
        balance_source=bs,
        block_resolver=_StaticBlockResolver(),
        nav_at_block=_const_nav,
        cap_usd=cap,
        # burn_date / end_date intentionally omitted.
    )
    by_date = {r["block_date"]: r for _, r in df.iterrows()}
    assert by_date[date(2026, 3, 8)]["cum_value"] == cap       # capped
    assert by_date[date(2026, 3, 9)]["cum_value"] == post      # below cap
    assert by_date[date(2026, 3, 12)]["cum_value"] == post     # below cap


def test_burn_date_without_end_date_raises():
    """burn_date is meaningless without end_date — refuse the call so the
    operator can't accidentally configure a half-set in-flight window."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    with pytest.raises(ValueError, match="end_date is None"):
        _sde_asset_value_timeseries(
            _prime(), _venue(), period,
            balance_source=_ConstBalanceSource(Decimal("100")),
            block_resolver=_StaticBlockResolver(),
            nav_at_block=_const_nav,
            cap_usd=Decimal("50"),
            burn_date=date(2026, 3, 9),
            end_date=None,
        )


def test_in_flight_window_outside_period_is_a_noop():
    """Burn occurred before the settlement period — every period day is
    post-end_date, so cum_value tracks on-chain throughout."""
    # Period: April 2026. Burn happened in March (pre-period).
    period = _period(date(2026, 4, 1), date(2026, 4, 30))
    cap = Decimal("325_000_000")
    post = Decimal("128_000_000")
    bs = _ConstBalanceSource(post)
    df = _sde_asset_value_timeseries(
        _prime(), _venue(), period,
        balance_source=bs,
        block_resolver=_StaticBlockResolver(),
        nav_at_block=_const_nav,
        cap_usd=cap,
        burn_date=date(2026, 3, 9),
        end_date=date(2026, 3, 12),
    )
    for _, row in df.iterrows():
        assert row["cum_value"] == post
        assert row["uncapped_value"] == post


def test_burn_date_without_cap_usd_raises():
    """In-flight cap-preservation needs a cap to pin. If someone configures
    burn_date on a non-capped entry (or otherwise passes cap_usd=None), the
    function should refuse rather than silently no-op the cap-preservation."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    with pytest.raises(ValueError, match="cap_usd is None"):
        _sde_asset_value_timeseries(
            _prime(), _venue(), period,
            balance_source=_ConstBalanceSource(Decimal("100")),
            block_resolver=_StaticBlockResolver(),
            nav_at_block=_const_nav,
            cap_usd=None,
            burn_date=date(2026, 3, 9),
            end_date=date(2026, 3, 12),
        )


def test_inverted_burn_window_raises():
    """burn_date > end_date is a YAML misconfiguration — the loop condition
    ``burn_date <= current <= end_date`` would silently match zero days.
    Refuse at the boundary so the operator gets a clear error."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    with pytest.raises(ValueError, match="after end_date"):
        _sde_asset_value_timeseries(
            _prime(), _venue(), period,
            balance_source=_ConstBalanceSource(Decimal("100")),
            block_resolver=_StaticBlockResolver(),
            nav_at_block=_const_nav,
            cap_usd=Decimal("50"),
            burn_date=date(2026, 3, 15),
            end_date=date(2026, 3, 12),   # before burn_date
        )


def test_cap_usd_none_returns_raw_uncapped_values():
    """When cap_usd is None and burn_date is also None, cum_value tracks the
    raw on-chain value every day (no cap applied, no in-flight handling)."""
    period = _period(date(2026, 3, 1), date(2026, 3, 31))
    raw = Decimal("454_000_000")
    bs = _ConstBalanceSource(raw)
    df = _sde_asset_value_timeseries(
        _prime(), _venue(), period,
        balance_source=bs,
        block_resolver=_StaticBlockResolver(),
        nav_at_block=_const_nav,
        cap_usd=None,
    )
    for _, row in df.iterrows():
        assert row["cum_value"] == raw
        assert row["uncapped_value"] == raw
