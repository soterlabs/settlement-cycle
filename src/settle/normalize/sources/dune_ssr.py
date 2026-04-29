"""Dune-backed `ISSRSource` — wraps `queries/ssr_history.sql`."""

from __future__ import annotations

from datetime import date

import pandas as pd

from ...extract.dune import execute_query
from ._dune_decode import to_decimal as _to_decimal
from ._paths import QUERIES_DIR


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
