"""Chronicle Points — 20% of the base rate on Chronicle Farm USDS (Grove).

Originally a port of the ``soterlabs/chronicle-points-dune-dash``
methodology (queries 7322636/7322608), computed per settlement month from
HyperSync Transfer logs instead of Dune. Since 2026-09-01 it follows the
repo's rate convention rather than the dash's, so the two series no longer
reconcile and the dashboard comparison has been retired::

    base_apr      = apy_to_apr(SSR_APY, n=12) + spread_d
    effective_apr = 0.20 × base_apr
    daily_accrual = farm_USDS_balance_d × effective_apr / 365

Two deliberate divergences from the dash: (1) SSR is converted from its
APY quote to a nominal APR before the spread is added, since the spread is
a governance APR (see ``_helpers.apy_to_apr``); (2) the accrual is nominal
— no intra-period compounding — matching the Base Rate this is a 20% share
of. ``spread_d`` also follows the repo's DATED schedule (30bps, 20bps from
2026-07-23) where the dash hardcodes a flat 30bps.

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
from ._helpers import apr_daily, apy_to_apr, cum_at_or_before, ssr_at_or_before
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
    # NOMINAL (APR) accrual, same convention as the Base Rate it is a share
    # of (2026-09-01): SSR converted to an APR at n=12, plus the dated
    # spread, times the 20% share, sliced ``x days/365`` with no intra-period
    # compounding. See ``_helpers.apy_to_apr``.
    total = Decimal("0")
    current = period.start
    while current <= period.end:
        balance = cum_at_or_before(farm_usds, "cum_balance", current)
        if balance > 0:
            base_apr = apy_to_apr(ssr_at_or_before(ssr, current)) + base_rate_spread_at(current)
            total += balance * apr_daily(_RATE_SHARE * base_apr)
        current = current + timedelta(days=1)
    return total
