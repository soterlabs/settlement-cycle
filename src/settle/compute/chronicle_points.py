"""Chronicle Points — 20% of the base rate on Chronicle Farm USDS (Grove).

Faithful port of the ``soterlabs/chronicle-points-dune-dash`` methodology
(queries 7322636/7322608), computed per settlement month from HyperSync
Transfer logs instead of Dune::

    base_rate_apy = SSR_APY + spread_d       (ADDITIVE, per the dash's
                                              formula; spread_d follows the
                                              repo's DATED schedule — 30bps,
                                              20bps from 2026-07-23 — per
                                              the MSC operator directive
                                              2026-08-04. The dash hardcodes
                                              a flat 30bps, so both series
                                              are identical through
                                              2026-07-22 and diverge only
                                              from the spread step.)
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
from .sky_revenue import base_rate_spread_at

_RATE_SHARE = Decimal("0.20")


def compute_chronicle_points(
    period: Period,
    farm_usds: pd.DataFrame,
    ssr: pd.DataFrame,
) -> Decimal:
    """Σ over the period's days of ``balance_d × daily_factor(0.20 × (SSR_d + spread_d))``.

    ``spread_d`` is the repo's dated BR−SSR schedule (30bps; 20bps from
    2026-07-23) — added ADDITIVELY per the dash's formula. ``farm_usds`` is
    a ``[block_date, daily_net, cum_balance]`` cumulative balance timeseries
    for (USDS, Chronicle Farm) — HyperSync-sourced by the orchestrator.
    ``ssr`` is the canonical SSR history frame.
    """
    total = Decimal("0")
    current = period.start
    while current <= period.end:
        balance = cum_at_or_before(farm_usds, "cum_balance", current)
        if balance > 0:
            base = ssr_at_or_before(ssr, current) + base_rate_spread_at(current)
            total += balance * daily_compounding_factor(_RATE_SHARE * base)
        current = current + timedelta(days=1)
    return total
