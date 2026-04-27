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
    ) -> pd.DataFrame:
        self.cumulative_calls.append((chain, token, holder, start, pin_block))
        return self.cumulative_df

    def directed_inflow_timeseries(
        self, chain: str, token: bytes, from_addr: bytes, to_addr: bytes,
        start: date, pin_block: int,
    ) -> pd.DataFrame:
        self.directed_calls.append((chain, token, from_addr, to_addr, start, pin_block))
        return self.directed_df


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
    """In-memory `IBlockResolver`. Returns canned block per (chain, anchor)."""

    blocks: dict[tuple[str, str], int] = field(default_factory=dict)
    default: int = 0
    calls: list[tuple] = field(default_factory=list)

    def block_at_or_before(self, chain: str, anchor_utc) -> int:
        self.calls.append((chain, anchor_utc))
        return self.blocks.get((chain, anchor_utc.isoformat()), self.default)
