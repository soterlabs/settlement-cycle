"""Sky revenue — interest the prime owes Sky on utilized USDS.

Per the prime-settlement-methodology and debt-rate-methodology docs:

    daily_sky_revenue = utilized × [(1 + base_rate)^(1/365) - 1]
    base_rate         = SSR + 30bps   (continuously compounded per-second)
    utilized          = cum_debt
                      − subproxy_usds                  ←  Step 2 (idle USDS at subproxy)
                      − subproxy_susds_principal       ←  prime doesn't pay base on subproxy holdings
                      − alm_proxy_usds                 ←  Step 2 (idle USDS at ALM proxy)
                      − psm_usds                       ←  Step 2 (idle USDS in PSM3)

``subproxy_susds_principal`` is the cost basis (``shares × entry_pps``), NOT
the current value — using current value would double-count SSR (the index
already reflects accrued savings the prime keeps). The orchestrator converts
shares → principal before passing in.

NOTE on what this function does NOT compute:
* Sky Direct shortfall (doc Step 4) is computed in the orchestrator
  (``compute_monthly_pnl``) as Σ ``vr.sky_direct_shortfall`` over the
  per-venue breakdown, then subtracted from this function's return value to
  get net Sky revenue. This function returns the *gross* charge on utilized.
* Idle USDS/DAI in lending pools / AMMs (doc Step 2) is not yet plumbed — no
  Grove venue currently holds USDS this way, so it's a $0 gap for Grove.
* Subsidised rate ramp (file 2 — first 24 months from prime start) is
  deferred to PRD §17.x; not implemented.

This function is pure — takes Normalize timeseries + period, returns USD `Decimal`.
The orchestrator (compute_monthly_pnl) is responsible for gathering inputs.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pandas as pd

from ..domain.period import Period
from ._helpers import (
    cum_at_or_before,
    daily_compounding_factor,
    require_non_empty,
    ssr_at_or_before,
)

# Spread Sky charges over SSR for utilized debt. Per prime-settlement-
# methodology §1 + debt-rate-methodology, the base rate = SSR + 30bps.
BASE_RATE_OVER_SSR = Decimal("0.003")


def compute_sky_revenue(
    period: Period,
    debt: pd.DataFrame,
    subproxy_usds: pd.DataFrame,
    subproxy_susds_principal: pd.DataFrame,
    alm_usds: pd.DataFrame,
    ssr: pd.DataFrame,
    psm_usds: pd.DataFrame | None = None,
) -> Decimal:
    """Sum of daily Sky revenue over ``period``.

    Inputs (all Normalize outputs):
    * ``debt``                       DataFrame[block_date, daily_dart, cum_debt]
    * ``subproxy_usds``              DataFrame[block_date, daily_net, cum_balance] — USDS in subproxy
    * ``subproxy_susds_principal``   ditto for sUSDS, in **USDS-equivalent cost basis**
                                     (``shares × entry_pps``), pre-converted by the orchestrator.
    * ``alm_usds``                   ditto for USDS in ALM proxy
    * ``ssr``                        DataFrame[effective_date, ssr_apy] — SP-BEAM changes
    * ``psm_usds``                   optional DataFrame[block_date, daily_net, cum_balance] —
                                     USDS the prime has parked at PSM (net of withdrawals);
                                     defaults to empty (= 0). When provided, subtracted from
                                     utilized so the prime is reimbursed BR on those holdings.
    """
    # Hard-fail on empty debt: without it, utilized = -subproxy ≤ 0 every day,
    # the loop short-circuits, and sky_revenue silently returns $0 — the most
    # likely cause is a misconfigured ilk_bytes32 or a failed Dune query, not
    # a prime that has never had any debt.
    require_non_empty(
        debt, name="debt",
        hint="Check `prime.ilk_bytes32` in the YAML and the IDebtSource impl.",
    )
    # SSR likewise — `ssr_at_or_before` already raises on lookup, but guarding
    # up front gives a clearer error pointing at the SSR source rather than at
    # whatever date triggers the daily lookup.
    require_non_empty(
        ssr, name="ssr_history",
        hint="Check the ISSRSource impl — SSR_HISTORY_ANCHOR may be wrong.",
    )

    total = Decimal("0")
    current = period.start
    while current <= period.end:
        cum_debt = cum_at_or_before(debt, "cum_debt", current)
        cum_sub_usds = cum_at_or_before(subproxy_usds, "cum_balance", current)
        cum_sub_susds = cum_at_or_before(subproxy_susds_principal, "cum_balance", current)
        cum_alm_usds = cum_at_or_before(alm_usds, "cum_balance", current)
        cum_psm_usds = cum_at_or_before(psm_usds, "cum_balance", current) if psm_usds is not None else Decimal("0")

        utilized = cum_debt - cum_sub_usds - cum_sub_susds - cum_alm_usds - cum_psm_usds

        if utilized > 0:
            ssr_apy = ssr_at_or_before(ssr, current)
            base_apy = ssr_apy + BASE_RATE_OVER_SSR
            total += utilized * daily_compounding_factor(base_apy)

        current = current + timedelta(days=1)

    return total
