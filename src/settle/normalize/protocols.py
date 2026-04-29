"""Source protocols — the interface between Normalize and Extract.

Each Source implementation knows where to pull raw data from (Dune, subgraph,
RPC, mock) and conforms to one of these Protocols. Normalize-layer functions
type-hint with the Protocol, so the source is swappable behind config.

Protocol args use primitive types only (``bytes``, ``str``, ``int``, ``date``)
so source implementations don't need to import domain types.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
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
        min_transfer_amount: Decimal = Decimal(0),
    ) -> pd.DataFrame:
        """Returns DataFrame[block_date, daily_net, cum_balance].

        ``min_transfer_amount`` is decimal-adjusted (human-readable units): any
        single transfer below this threshold is dropped before aggregation.
        Used by BUIDL-style venues to separate small daily yield-distribution
        mints from real capital deposits.
        """
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

    def inflow_by_counterparty(
        self,
        chain: str,
        token: bytes,
        holder: bytes,
        start: date,
        pin_block: int,
    ) -> pd.DataFrame:
        """Per-day per-counterparty signed flow at ``holder``.

        Returns DataFrame[block_date, counterparty, signed_amount]:
        ``counterparty`` is the other side of the transfer (``from`` for an
        inflow, ``to`` for an outflow); ``signed_amount`` is positive on
        inflow, negative on outflow.

        Used by Cat A source-tagged inflow tracking — the compute layer then
        classifies each row's counterparty against the prime's
        ``external_alm_sources`` allowlist (off-chain custodians sending
        realized yield) and nets every other counterparty as value-preserving
        capital flow.
        """
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
    """Resolve block numbers ↔ UTC dates.

    Used by ``compute_monthly_pnl`` to pin SoM and EoM blocks per chain, and
    to bucket per-event timeseries (e.g. V3 liquidity events) by date. A
    Protocol (rather than a direct call to ``extract.rpc``) so future
    implementations (subgraph, indexer cache) plug in via the same interface.
    """

    def block_at_or_before(self, chain: str, anchor_utc: datetime) -> int: ...

    def block_to_date(self, chain: str, block: int) -> date:
        """UTC calendar date of the given block."""
        ...


class INavOracleSource(Protocol):
    """Read NAV (USD price-per-token) for a Category-E venue at a specific block.

    Implementations exist per oracle kind (Chronicle, Redstone, Chainlink, Pyth,
    plus ``const_one`` for venues whose NAV is pinned to $1 by config).

    Sources raise on failure (e.g. allowlist revert); the ``get_unit_price``
    branch in ``normalize.prices`` catches and walks the venue's configured
    fallback chain (``nav_oracle.fallback`` in YAML).
    """

    def nav_at(
        self,
        chain: str,
        oracle_address: bytes | None,
        block: int,
    ) -> Decimal: ...


class IV3PositionSource(Protocol):
    """Enumerate Uniswap V3 NFT positions a holder owns in a target pool, plus
    track the liquidity flows in/out of those positions over a block range.

    Returns each position's raw redeemable amounts: liquidity-implied principal
    plus materialized ``tokensOwed`` plus pending fees from ``feeGrowthInside``
    deltas. Used by the Cat F (Uniswap V3) branch of ``get_position_value`` and
    by per-venue inflow tracking in ``compute_monthly_pnl``.
    """

    def positions_in_pool(
        self,
        chain: str,
        owner: bytes,
        pool: bytes,
        block: int,
    ) -> list:
        """Returns list[V3PositionAmounts] — sized 0 when the holder has no
        positions in the target pool."""
        ...

    def liquidity_events_in_pool(
        self,
        chain: str,
        owner: bytes,
        pool: bytes,
        from_block: int,
        to_block: int,
    ) -> list:
        """Returns list[V3LiquidityEvent] for every position in the target
        pool, across (from_block, to_block]. Signed amounts: ``+`` on
        ``IncreaseLiquidity``, ``-`` on ``DecreaseLiquidity``."""
        ...


class ICurvePoolSource(Protocol):
    """Read Curve stableswap pool state + Add/Remove liquidity events.

    Used by the Cat F (Curve LP) branch of ``get_position_value`` and by
    per-venue inflow tracking in ``compute_monthly_pnl``. The two methods are
    independent — value pricing only needs ``read_pool``.
    """

    def read_pool(self, chain: str, pool_address: bytes, block: int):
        """Returns a ``CurvePoolState``-shaped object (virtual_price_raw,
        total_supply, coins, balances)."""
        ...

    def liquidity_events_for_provider(
        self,
        chain: str,
        pool_address: bytes,
        provider: bytes,
        from_block: int,
        to_block: int,
    ) -> list:
        """Returns list[CurveLiquidityEvent] across (from_block, to_block].
        Used for the (currently dead) event-based Curve inflow path."""
        ...
