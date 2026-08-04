"""Chronicle Points — 20% of the base rate on Chronicle Farm USDS (Grove).

Faithful port of the ``soterlabs/chronicle-points-dune-dash`` methodology
(queries 7322636/7322608), computed per settlement month from HyperSync
Transfer logs instead of Dune::

    base_rate_apy = SSR_APY + 0.003          (ADDITIVE, flat 30bps — the
                                              dash's formula verbatim; it
                                              does NOT follow this repo's
                                              multiplicative ⊕ convention
                                              nor the 2026-07-23 20bps
                                              spread step)
    effective_apy = 0.20 × base_rate_apy
    daily_accrual = farm_USDS_balance_d × ((1 + effective_apy)^(1/365) − 1)

``farm_USDS_balance_d`` is the day's END-of-day balance of the Chronicle
Farm (StakingRewards) contract, reconstructed from USDS Transfer logs with
carry-forward on quiet days — the same shape as the dash's running-sum +
gap-fill. SSR is the repo's canonical per-day series (last ``file()`` per
UTC day, carry-forward), identical to the dash's Step 1.

Enabled per prime via the ``chronicle_points:`` config block (Grove only
today). The monthly total is a Demand-Side revenue component: it sums with
``agent_rate`` and ``distribution_rewards`` into demand-side revenue /
``prime_agent_total_revenue``.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pandas as pd

from ..domain.period import Period
from ._helpers import cum_at_or_before, daily_compounding_factor, ssr_at_or_before

# The dash's rate constants — deliberately hardcoded to mirror the source
# queries, NOT wired to BASE_RATE_SPREAD_SCHEDULE. If Chronicle's program
# adopts the dated spread, change here and note the effective date.
_SPREAD_ADDITIVE = Decimal("0.003")
_RATE_SHARE = Decimal("0.20")


def compute_chronicle_points(
    period: Period,
    farm_usds: pd.DataFrame,
    ssr: pd.DataFrame,
) -> Decimal:
    """Σ over the period's days of ``balance_d × daily_factor(0.20 × (SSR_d + 30bps))``.

    ``farm_usds`` is a ``[block_date, daily_net, cum_balance]`` cumulative
    balance timeseries for (USDS, Chronicle Farm) — HyperSync-sourced by the
    orchestrator. ``ssr`` is the canonical SSR history frame.
    """
    total = Decimal("0")
    current = period.start
    while current <= period.end:
        balance = cum_at_or_before(farm_usds, "cum_balance", current)
        if balance > 0:
            base = ssr_at_or_before(ssr, current) + _SPREAD_ADDITIVE
            total += balance * daily_compounding_factor(_RATE_SHARE * base)
        current = current + timedelta(days=1)
    return total
