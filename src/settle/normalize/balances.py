"""Canonical balance / inflow timeseries primitives."""

from __future__ import annotations

import pandas as pd

from ..domain.period import Period
from ..domain.primes import Address, Chain, Prime, Token
from ..validation.schemas import assert_columns
from .protocols import IBalanceSource
from .registry import get_balance_source


def _resolve_pin(period: Period, chain: Chain) -> int:
    if chain not in period.pin_blocks:
        raise ValueError(
            f"Period missing pin_block for chain {chain.value}; "
            f"have {sorted(c.value for c in period.pin_blocks)}"
        )
    return period.pin_blocks[chain]


def _cumulative(
    src: IBalanceSource, chain: Chain, token: Token, holder: Address,
    start_date, pin_block: int,
) -> pd.DataFrame:
    df = src.cumulative_balance_timeseries(
        chain=chain.value,
        token=token.address.value,
        holder=holder.value,
        start=start_date,
        pin_block=pin_block,
    )
    assert_columns(df, ["block_date", "daily_net", "cum_balance"])
    return df


def get_subproxy_balance_timeseries(
    prime: Prime,
    chain: Chain,
    token: Token,
    period: Period,
    *,
    source: IBalanceSource | None = None,
) -> pd.DataFrame:
    """Daily net flow + running balance of `token` held by `prime.subproxy[chain]`."""
    if chain not in prime.subproxy:
        raise ValueError(f"Prime {prime.id!r} has no subproxy on {chain.value}")
    src = source if source is not None else get_balance_source()
    return _cumulative(
        src, chain, token, prime.subproxy[chain], prime.start_date, _resolve_pin(period, chain),
    )


def get_alm_balance_timeseries(
    prime: Prime,
    chain: Chain,
    token: Token,
    period: Period,
    *,
    source: IBalanceSource | None = None,
) -> pd.DataFrame:
    """Daily net flow + running balance of `token` held by `prime.alm[chain]`."""
    if chain not in prime.alm:
        raise ValueError(f"Prime {prime.id!r} has no ALM on {chain.value}")
    src = source if source is not None else get_balance_source()
    return _cumulative(
        src, chain, token, prime.alm[chain], prime.start_date, _resolve_pin(period, chain),
    )


def get_venue_inflow_timeseries(
    prime: Prime,
    chain: Chain,
    underlying: Token,
    venue_addr: Address,
    period: Period,
    *,
    source: IBalanceSource | None = None,
) -> pd.DataFrame:
    """Cumulative ALM→venue token inflow (cost-basis input).

    Tracks the underlying token (e.g. USDC for OBEX → Maple) flowing from
    `prime.alm[chain]` to `venue_addr`.
    """
    if chain not in prime.alm:
        raise ValueError(f"Prime {prime.id!r} has no ALM on {chain.value}")
    src = source if source is not None else get_balance_source()
    df = src.directed_inflow_timeseries(
        chain=chain.value,
        token=underlying.address.value,
        from_addr=prime.alm[chain].value,
        to_addr=venue_addr.value,
        start=prime.start_date,
        pin_block=_resolve_pin(period, chain),
    )
    assert_columns(df, ["block_date", "daily_inflow", "cum_inflow"])
    return df
