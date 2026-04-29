"""Mock implementations of the Normalize source Protocols.

Used by unit tests to verify Normalize primitives without hitting Dune.
Each mock records its call args so tests can assert what the primitive
passed down to the source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass
class MockDebtSource:
    """In-memory `IDebtSource`. Returns a canned DataFrame."""

    df: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(
            {"block_date": [], "daily_dart": [], "cum_debt": []}
        )
    )
    calls: list[tuple] = field(default_factory=list)

    def debt_timeseries(self, ilk: bytes, start: date, pin_block: int) -> pd.DataFrame:
        self.calls.append((ilk, start, pin_block))
        return self.df


@dataclass
class MockBalanceSource:
    """In-memory `IBalanceSource` — records cumulative + directed calls."""

    cumulative_df: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(
            {"block_date": [], "daily_net": [], "cum_balance": []}
        )
    )
    directed_df: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(
            {"block_date": [], "daily_inflow": [], "cum_inflow": []}
        )
    )
    cumulative_calls: list[tuple] = field(default_factory=list)
    directed_calls: list[tuple] = field(default_factory=list)

    def cumulative_balance_timeseries(
        self, chain: str, token: bytes, holder: bytes, start: date, pin_block: int,
        min_transfer_amount=None,
    ) -> pd.DataFrame:
        self.cumulative_calls.append(
            (chain, token, holder, start, pin_block, min_transfer_amount)
        )
        return self.cumulative_df

    def directed_inflow_timeseries(
        self, chain: str, token: bytes, from_addr: bytes, to_addr: bytes,
        start: date, pin_block: int,
    ) -> pd.DataFrame:
        self.directed_calls.append((chain, token, from_addr, to_addr, start, pin_block))
        return self.directed_df

    # Optional per-counterparty inflow detail — overridden by tests that
    # exercise the Cat A source-tagged path. Default returns an empty frame.
    def inflow_by_counterparty(
        self, chain: str, token: bytes, holder: bytes,
        start: date, pin_block: int,
    ) -> pd.DataFrame:
        return pd.DataFrame({
            "block_date": [], "counterparty": [], "signed_amount": [],
        })


@dataclass
class MockSSRSource:
    """In-memory `ISSRSource`."""

    df: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame({"effective_date": [], "ssr_apy": []})
    )
    calls: list[tuple] = field(default_factory=list)

    def ssr_history(self, start: date, pin_block: int) -> pd.DataFrame:
        self.calls.append((start, pin_block))
        return self.df


@dataclass
class MockPositionBalanceSource:
    """In-memory `IPositionBalanceSource`. Returns a fixed raw balance."""

    raw_balance: int = 0
    calls: list[tuple] = field(default_factory=list)

    def balance_at(self, chain: str, token: bytes, holder: bytes, block: int) -> int:
        self.calls.append((chain, token, holder, block))
        return self.raw_balance


@dataclass
class MockConvertToAssetsSource:
    """In-memory `IConvertToAssetsSource`. Returns ``shares × scale`` so tests can
    assert pricing math in closed form."""

    raw_assets: int = 0
    calls: list[tuple] = field(default_factory=list)

    def convert_to_assets(self, chain: str, vault: bytes, shares: int, block: int) -> int:
        self.calls.append((chain, vault, shares, block))
        return self.raw_assets


@dataclass
class MockBlockResolver:
    """In-memory `IBlockResolver`. Returns canned block per (chain, anchor)
    and canned date per (chain, block)."""

    blocks: dict[tuple[str, str], int] = field(default_factory=dict)
    default: int = 0
    dates_by_block: dict[tuple[str, int], date] = field(default_factory=dict)
    default_date: date = date(2026, 3, 15)
    calls: list[tuple] = field(default_factory=list)
    block_to_date_calls: list[tuple] = field(default_factory=list)

    def block_at_or_before(self, chain: str, anchor_utc) -> int:
        self.calls.append((chain, anchor_utc))
        return self.blocks.get((chain, anchor_utc.isoformat()), self.default)

    def block_to_date(self, chain: str, block: int) -> date:
        self.block_to_date_calls.append((chain, block))
        return self.dates_by_block.get((chain, block), self.default_date)


@dataclass
class MockNavOracleSource:
    """In-memory `INavOracleSource`. Returns canned NAVs or raises on demand."""

    nav: object = None         # `Decimal` to return, or an Exception class to raise
    calls: list[tuple] = field(default_factory=list)

    def nav_at(self, chain: str, oracle_address, block: int):
        from decimal import Decimal as _D
        self.calls.append((chain, oracle_address, block))
        if isinstance(self.nav, type) and issubclass(self.nav, BaseException):
            raise self.nav("mock-configured failure")
        if self.nav is None:
            return _D("1.00")
        return self.nav


@dataclass
class MockV3PositionSource:
    """In-memory `IV3PositionSource`.

    ``positions_by_block`` maps a block number to the list of
    ``V3PositionAmounts`` to return at that block. ``default`` is used for any
    block not in the map (defaults to empty list). Use this for tests that
    snapshot at SoM and EoM with different position states.

    ``liquidity_events`` is a flat list of ``V3LiquidityEvent`` returned
    verbatim by ``liquidity_events_in_pool``. Tests that don't exercise the
    inflow path can leave it empty.
    """

    positions_by_block: dict[int, list] = field(default_factory=dict)
    default: list = field(default_factory=list)
    liquidity_events: list = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)
    inflow_calls: list[tuple] = field(default_factory=list)

    def positions_in_pool(
        self, chain: str, owner: bytes, pool: bytes, block: int,
    ) -> list:
        self.calls.append((chain, owner, pool, block))
        return self.positions_by_block.get(block, self.default)

    def liquidity_events_in_pool(
        self, chain: str, owner: bytes, pool: bytes,
        from_block: int, to_block: int,
    ) -> list:
        self.inflow_calls.append((chain, owner, pool, from_block, to_block))
        return list(self.liquidity_events)
