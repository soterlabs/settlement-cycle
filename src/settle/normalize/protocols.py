"""Source protocols — the interface between Normalize and Extract.

Each Source implementation knows where to pull raw data from (Dune, subgraph,
RPC, mock) and conforms to one of these Protocols. Normalize-layer functions
type-hint with the Protocol, so the source is swappable behind config.

Protocol args use primitive types only (``bytes``, ``str``, ``int``, ``date``)
so source implementations don't need to import domain types.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

import pandas as pd


class IDebtSource(Protocol):
    """Daily cumulative ilk debt timeseries."""

    def debt_timeseries(self, ilk: bytes, start: date, pin_block: int) -> pd.DataFrame:
        """Returns DataFrame[block_date, daily_dart, cum_debt]."""
        ...


class IBalanceSource(Protocol):
    """Token balance flows for a holder + directed flows between two addresses."""

    def cumulative_balance_timeseries(
        self,
        chain: str,
        token: bytes,
        holder: bytes,
        start: date,
        pin_block: int,
    ) -> pd.DataFrame:
        """Returns DataFrame[block_date, daily_net, cum_balance]."""
        ...

    def directed_inflow_timeseries(
        self,
        chain: str,
        token: bytes,
        from_addr: bytes,
        to_addr: bytes,
        start: date,
        pin_block: int,
    ) -> pd.DataFrame:
        """Returns DataFrame[block_date, daily_inflow, cum_inflow]."""
        ...


class ISSRSource(Protocol):
    """SSR rate boundaries via SP-BEAM file() calls on sUSDS."""

    def ssr_history(self, start: date, pin_block: int) -> pd.DataFrame:
        """Returns DataFrame[effective_date, ssr_apy]."""
        ...


class IPositionBalanceSource(Protocol):
    """Snapshot ERC-20 balance at a specific block.

    For rebasing tokens (aTokens, spTokens) this returns the *rebased* nominal
    amount — interest already accrued, ready to multiply by underlying price.
    """

    def balance_at(self, chain: str, token: bytes, holder: bytes, block: int) -> int:
        """Returns raw on-chain balance (smallest unit, no decimal adjustment)."""
        ...


class IConvertToAssetsSource(Protocol):
    """ERC-4626 ``convertToAssets(shares)``. Used by Category B pricing."""

    def convert_to_assets(self, chain: str, vault: bytes, shares: int, block: int) -> int:
        """Returns raw underlying-asset amount for the given share quantity."""
        ...


class IBlockResolver(Protocol):
    """Resolve the highest block number with timestamp ≤ ``anchor_utc``.

    Used by ``compute_monthly_pnl`` to pin SoM and EoM blocks per chain. A
    Protocol (rather than a direct call to ``extract.rpc``) so future
    implementations (subgraph, indexer cache) plug in via the same interface.
    """

    def block_at_or_before(self, chain: str, anchor_utc: datetime) -> int: ...
