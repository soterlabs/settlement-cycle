"""Dune-backed `ISavingsV2DeployedSource` — wraps `queries/savings_v2_deployed.sql`.

TODO: The upstream Dune table `dune.sparkdotfi.result_savings_v_2_deployment_metrics`
is no longer publicly accessible (private dataset).  The implementation is stubbed
to return an empty DataFrame until a replacement data source is available.

To re-enable:
  1. Identify or recreate a data source for daily sUSDS deployed from the Spark ETH
     ALM into Savings V2 (the `deployed_amount` / `spUSDC` time-series).
  2. Replace the stub below with a live query against that source.
  3. Re-run the affected settlement periods to backfill corrected S32 values.

Without this deduction, S32 value_som / value_eom are slightly overstated (they
include sUSDS shares deployed into Savings V2 that are not held at the ALM proxy).
"""

from __future__ import annotations

import logging

import pandas as pd

_log = logging.getLogger(__name__)


class DuneSavingsV2DeployedSource:
    """Stubbed until a replacement data source for Savings V2 deployment metrics
    is available.  Returns an empty DataFrame so the deduction is silently skipped.
    See module docstring for re-enablement instructions.
    """

    def savings_v2_deployed(self, pin_block: int) -> pd.DataFrame:
        _log.warning(
            "DuneSavingsV2DeployedSource: Savings V2 deployment metrics data source "
            "is unavailable (upstream Dune table removed). Returning empty — "
            "deduct_savings_v2_deployed will have no effect until a replacement "
            "data source is implemented. See normalize/sources/dune_savings_v2_deployed.py."
        )
        return pd.DataFrame(columns=["dt", "susds_deployed_usd"])
