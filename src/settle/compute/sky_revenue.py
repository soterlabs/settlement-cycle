"""Sky revenue — interest the prime owes Sky on utilized USDS.

Per RULES.md §4:

    daily_sky_revenue = utilized_usds × [(1 + borrow_rate)^(1/365) - 1]
    borrow_rate       = SSR + 0.30%
    utilized_usds     = cum_debt − subproxy_usds − subproxy_susds − alm_proxy_usds

This function is pure — takes Normalize timeseries + period, returns USD `Decimal`.
The orchestrator (compute_monthly_pnl) is responsible for gathering inputs.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pandas as pd

from ..domain.period import Period
from ._helpers import cum_at_or_before, daily_compounding_factor, ssr_at_or_before

BORROW_RATE_SPREAD = Decimal("0.003")  # SSR + 0.30%


def compute_sky_revenue(
    period: Period,
    debt: pd.DataFrame,
    subproxy_usds: pd.DataFrame,
    subproxy_susds: pd.DataFrame,
    alm_usds: pd.DataFrame,
    ssr: pd.DataFrame,
) -> Decimal:
    """Sum of daily Sky revenue over ``period``.

    Inputs (all Normalize outputs):
    * ``debt``          DataFrame[block_date, daily_dart, cum_debt]
    * ``subproxy_usds`` DataFrame[block_date, daily_net, cum_balance] — USDS in subproxy
    * ``subproxy_susds`` ditto for sUSDS
    * ``alm_usds``      ditto for USDS in ALM proxy
    * ``ssr``           DataFrame[effective_date, ssr_apy] — SP-BEAM changes
    """
    total = Decimal("0")
    current = period.start
    while current <= period.end:
        cum_debt = cum_at_or_before(debt, "cum_debt", current)
        cum_sub_usds = cum_at_or_before(subproxy_usds, "cum_balance", current)
        cum_sub_susds = cum_at_or_before(subproxy_susds, "cum_balance", current)
        cum_alm_usds = cum_at_or_before(alm_usds, "cum_balance", current)

        utilized = cum_debt - cum_sub_usds - cum_sub_susds - cum_alm_usds

        if utilized > 0:
            ssr_apy = ssr_at_or_before(ssr, current)
            borrow_apy = ssr_apy + BORROW_RATE_SPREAD
            total += utilized * daily_compounding_factor(borrow_apy)

        current = current + timedelta(days=1)

    return total
