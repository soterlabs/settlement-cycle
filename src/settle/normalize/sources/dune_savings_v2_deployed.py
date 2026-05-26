"""Dune-backed `ISavingsV2DeployedSource` — wraps `queries/savings_v2_deployed.sql`."""

from __future__ import annotations

import pandas as pd

from ...extract.dune import execute_query
from ._dune_decode import to_decimal as _to_decimal
from ._paths import QUERIES_DIR


class DuneSavingsV2DeployedSource:
    """Implements `ISavingsV2DeployedSource` against savings_v2_deployed.sql."""

    def savings_v2_deployed(self, pin_block: int) -> pd.DataFrame:
        df = execute_query(
            QUERIES_DIR / "savings_v2_deployed.sql",
            params={},
            pin_block=pin_block,
        )
        if df.empty:
            return df
        df["dt"] = pd.to_datetime(df["dt"]).dt.date
        df["susds_deployed_usd"] = df["susds_deployed_usd"].apply(_to_decimal)
        df = df.sort_values("dt").reset_index(drop=True)
        return df
