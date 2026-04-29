"""Dune-backed `IBalanceSource`. Wraps `transfer_timeseries.sql` + `venue_inflow.sql`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from ...extract.dune import execute_query
from ._dune_decode import to_addr_bytes as _to_addr_bytes, to_decimal as _to_decimal
from ._paths import QUERIES_DIR



class DuneBalanceSource:
    """Implements `IBalanceSource` against the shared transfer queries."""

    def cumulative_balance_timeseries(
        self,
        chain: str,
        token: bytes,
        holder: bytes,
        start: date,
        pin_block: int,
        min_transfer_amount: Decimal = Decimal(0),
    ) -> pd.DataFrame:
        df = execute_query(
            QUERIES_DIR / "transfer_timeseries.sql",
            params={
                "chain": chain,
                "token": token,
                "holder": holder,
                "start_date": start.isoformat(),
                # Pass as Decimal so ``_format_param`` types it as ``number``
                # — the SQL uses it in a numeric comparison
                # (``AND amount >= {{min_transfer_amount}}``).
                "min_transfer_amount": min_transfer_amount,
            },
            pin_block=pin_block,
        )
        if df.empty:
            return df
        df["block_date"] = pd.to_datetime(df["block_date"]).dt.date
        df["daily_net"] = df["daily_net"].apply(_to_decimal)
        df["cum_balance"] = df["cum_balance"].apply(_to_decimal)
        df = df.sort_values("block_date").reset_index(drop=True)
        return df

    def directed_inflow_timeseries(
        self,
        chain: str,
        token: bytes,
        from_addr: bytes,
        to_addr: bytes,
        start: date,
        pin_block: int,
    ) -> pd.DataFrame:
        df = execute_query(
            QUERIES_DIR / "venue_inflow.sql",
            params={
                "chain": chain,
                "token": token,
                "from_addr": from_addr,
                "to_addr": to_addr,
                "start_date": start.isoformat(),
            },
            pin_block=pin_block,
        )
        if df.empty:
            return df
        df["block_date"] = pd.to_datetime(df["block_date"]).dt.date
        df["daily_inflow"] = df["daily_inflow"].apply(_to_decimal)
        df["cum_inflow"] = df["cum_inflow"].apply(_to_decimal)
        df = df.sort_values("block_date").reset_index(drop=True)
        return df

    def inflow_by_counterparty(
        self,
        chain: str,
        token: bytes,
        holder: bytes,
        start: date,
        pin_block: int,
    ) -> pd.DataFrame:
        df = execute_query(
            QUERIES_DIR / "inflow_by_counterparty.sql",
            params={
                "chain": chain,
                "token": token,
                "holder": holder,
                "start_date": start.isoformat(),
            },
            pin_block=pin_block,
        )
        if df.empty:
            return df
        df["block_date"] = pd.to_datetime(df["block_date"]).dt.date
        df["counterparty"] = df["counterparty"].apply(_to_addr_bytes)
        df["signed_amount"] = df["signed_amount"].apply(_to_decimal)
        df = df.sort_values(["block_date"]).reset_index(drop=True)
        return df
