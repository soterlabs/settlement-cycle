"""Dune-backed `IDebtSource` — wraps `queries/debt_timeseries.sql`."""

from __future__ import annotations

from datetime import date

import pandas as pd

from ...extract.dune import execute_query
from ._dune_decode import to_decimal as _to_decimal
from ._paths import QUERIES_DIR


class DuneDebtSource:
    """Implements `IDebtSource` against the shared `debt_timeseries.sql`."""

    def debt_timeseries(self, ilk: bytes, start: date, pin_block: int) -> pd.DataFrame:
        df = execute_query(
            QUERIES_DIR / "debt_timeseries.sql",
            params={"ilk_bytes32": ilk, "start_date": start.isoformat()},
            pin_block=pin_block,
        )
        if df.empty:
            return df
        df["block_date"] = pd.to_datetime(df["block_date"]).dt.date
        # Carry numerics as Decimal — Compute consumes them as Decimal and float
        # round-trip would lose ~3 sig figs (USD precision contract, PRD §10).
        df["daily_dart"] = df["daily_dart"].apply(_to_decimal)
        df["cum_debt"] = df["cum_debt"].apply(_to_decimal)
        # Defensive: don't rely on Dune row order for carry-forward lookups.
        df = df.sort_values("block_date").reset_index(drop=True)
        return df
