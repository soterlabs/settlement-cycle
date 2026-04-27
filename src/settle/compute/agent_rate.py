"""Agent rate — earnings owed to the prime on its subproxy's idle holdings.

Per RULES.md §3:

    daily_agent_rate = subproxy_usds  × [(1 + SSR + 0.20%)^(1/365) - 1]
                     + subproxy_susds × [(1.002)^(1/365)        - 1]

USDS in the subproxy earns SSR + 0.20% APY.
sUSDS in the subproxy earns a flat 0.20% APY (NOT SSR-based).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pandas as pd

from ..domain.period import Period
from ._helpers import cum_at_or_before, daily_compounding_factor, ssr_at_or_before

SUBPROXY_USDS_SPREAD = Decimal("0.002")    # SSR + 0.20%
SUBPROXY_SUSDS_FLAT_APY = Decimal("0.002")  # flat 0.20% on sUSDS

# Pre-compute the sUSDS daily factor — it doesn't depend on SSR.
_SUSDS_DAILY_FACTOR = daily_compounding_factor(SUBPROXY_SUSDS_FLAT_APY)


def compute_agent_rate(
    period: Period,
    subproxy_usds: pd.DataFrame,
    subproxy_susds: pd.DataFrame,
    ssr: pd.DataFrame,
) -> Decimal:
    """Sum of daily agent rate over ``period``."""
    total = Decimal("0")
    current = period.start
    while current <= period.end:
        cum_usds = cum_at_or_before(subproxy_usds, "cum_balance", current)
        cum_susds = cum_at_or_before(subproxy_susds, "cum_balance", current)

        # USDS earns SSR + 0.20%; only need the SSR if we have USDS.
        if cum_usds > 0:
            ssr_apy = ssr_at_or_before(ssr, current)
            usds_apy = ssr_apy + SUBPROXY_USDS_SPREAD
            total += cum_usds * daily_compounding_factor(usds_apy)

        if cum_susds > 0:
            total += cum_susds * _SUSDS_DAILY_FACTOR

        current = current + timedelta(days=1)

    return total
