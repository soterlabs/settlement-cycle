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
    som_block: int | None = None,
    balance_at=None,
) -> pd.DataFrame:
    """Daily net flow + running balance of `token` held by `prime.subproxy[chain]`.

    When ``som_block`` and ``balance_at`` are supplied, the series is
    anchored against on-chain ``balanceOf`` at the SoM block. The
    Dune-based ``cumulative_balance_timeseries`` reconstructs the balance
    from transfer events starting at ``prime.start_date``, but the
    SubProxy may hold a pre-existing balance from before that date
    (Spark's SubProxy held ~$30–37M USDS throughout 2026 funded via Sky
    governance allocations whose Transfer events Dune doesn't surface
    for this address). The on-chain ``balanceOf`` is the gold standard;
    the seed adjustment shifts the entire series to match.
    """
    from decimal import Decimal as _Dec
    import logging as _logging

    if chain not in prime.subproxy:
        raise ValueError(f"Prime {prime.id!r} has no subproxy on {chain.value}")
    src = source if source is not None else get_balance_source()
    df = _cumulative(
        src, chain, token, prime.subproxy[chain], prime.start_date, _resolve_pin(period, chain),
    )

    if som_block is not None and balance_at is not None:
        scale = _Dec(10 ** token.decimals)
        on_chain_som_raw = balance_at(
            chain.value, token.address.value,
            prime.subproxy[chain].value, som_block,
        )
        on_chain_som = _Dec(on_chain_som_raw) / scale

        # Tracked balance at the SoM cutover. Pick the last row with
        # block_date < period.start (= the events-tracked balance going
        # into the period).
        if df.empty:
            tracked_som = _Dec("0")
        else:
            mask = df["block_date"] < period.start
            tracked_som = (
                _Dec(str(df.loc[mask, "cum_balance"].iloc[-1]))
                if mask.any() else _Dec("0")
            )

        seed = on_chain_som - tracked_som
        if abs(seed) > _Dec("0.01"):
            _logging.getLogger(__name__).warning(
                "get_subproxy_balance_timeseries: SoM anchor found "
                "%.6f-token gap between Dune-tracked cum_balance (%.6f) "
                "and on-chain balanceOf (%.6f) at subproxy %s on %s. "
                "Anchoring series to on-chain truth — most likely cause: "
                "pre-period funding not captured by Dune tokens.transfers.",
                float(seed), float(tracked_som), float(on_chain_som),
                prime.subproxy[chain].hex, token.symbol,
            )
            # Shift all existing rows by the seed AND prepend a synthetic
            # row at prime.start_date so ``cum_at_or_before`` returns the
            # seed for dates with no events (the empty-series case). Use
            # Decimal arithmetic — different balance sources return
            # ``cum_balance`` as Decimal (Grove) or float (Spark fixture).
            if not df.empty:
                df = df.copy()
                df["cum_balance"] = df["cum_balance"].apply(
                    lambda v: _Dec(str(v)) + seed
                )
            seed_row = pd.DataFrame([{
                "block_date": prime.start_date,
                "daily_net":  _Dec("0"),
                "cum_balance": seed,
            }])
            # Filter out any existing row at start_date to avoid duplication.
            if not df.empty:
                df = df[df["block_date"] != prime.start_date]
            df = pd.concat([seed_row, df], ignore_index=True)
    return df


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
