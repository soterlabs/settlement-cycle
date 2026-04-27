"""Dune-backed `ISSRSource` — wraps `queries/ssr_history.sql`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from ...extract.dune import execute_query
from ._paths import QUERIES_DIR


def _to_decimal(v: object) -> Decimal:
    return Decimal(str(v))


class DuneSSRSource:
    """Implements `ISSRSource` against the shared `ssr_history.sql`."""

    def ssr_history(self, start: date, pin_block: int) -> pd.DataFrame:
        df = execute_query(
            QUERIES_DIR / "ssr_history.sql",
            params={"start_date": start.isoformat()},
            pin_block=pin_block,
        )
        if df.empty:
            return df
        df["effective_date"] = pd.to_datetime(df["effective_date"]).dt.date
        df["ssr_apy"] = df["ssr_apy"].apply(_to_decimal)
        df = df.sort_values("effective_date").reset_index(drop=True)
        return df
