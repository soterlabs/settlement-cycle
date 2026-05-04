"""Sky revenue — interest the prime owes Sky on utilized USDS.

Per the prime-settlement-methodology and debt-rate-methodology docs:

    daily_sky_revenue = utilized × [(1 + apy)^(1/365) - 1]
    apy               = base_apy (default) | subsidised_apy (when enabled)
    base_apy          = SSR + 30bps
    subsidised_apy    = ref_rate + (base − ref_rate) × T / 24    [Step 1.b]
    utilized          = cum_debt
                      − subproxy_usds                  ←  Step 2 (idle USDS at subproxy)
                      − subproxy_susds_principal       ←  prime doesn't pay base on subproxy holdings
                      − alm_proxy_usds                 ←  Step 2 (idle USDS at ALM proxy)
                      − psm_usds                       ←  Step 2 (idle USDS in PSM3)

``subproxy_susds_principal`` is the cost basis (``shares × entry_pps``), NOT
the current value — using current value would double-count SSR (the index
already reflects accrued savings the prime keeps). The orchestrator converts
shares → principal before passing in.

When ``subsidy_config.enabled`` is True:
* The first ``subsidy_config.cap_usd`` of utilized is charged at the
  subsidised rate; any excess at the full base rate.
* T = months elapsed since ``subsidy_config.program_start`` (default
  2026-01-01). Jan 2026 → T=0, Feb 2026 → T=1, …
* ``ref_rate_history`` provides the daily reference rate (EFFR or 3M T-Bill).

NOTE on what this function does NOT compute:
* Sky Direct revenue (doc Step 4) is computed in the orchestrator from the
  per-venue breakdown (Σ ``vr.sd_revenue``) and added to this function's
  return value. This function returns BR on (utilized − SDE asset value);
  the caller composes it with sde_revenue to form gross sky_revenue.
* Idle USDS/DAI in lending pools / AMMs (doc Step 2) is not yet plumbed — no
  Grove venue currently holds USDS this way, so it's a $0 gap for Grove.

This function is pure — takes Normalize timeseries + period, returns USD `Decimal`.
The orchestrator (compute_monthly_pnl) is responsible for gathering inputs.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pandas as pd

from ..domain.period import Period
from ..domain.subsidy import (
    ReferenceRateHistory,
    SubsidyConfig,
    months_elapsed_since,
    subsidised_apy,
)
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
    *,
    subsidy_config: SubsidyConfig | None = None,
    ref_rate_history: ReferenceRateHistory | None = None,
    sde_asset_value: pd.DataFrame | None = None,
) -> Decimal:
    """Sum of daily Sky revenue over ``period``.

    Inputs (all Normalize outputs):
    * ``debt``                       DataFrame[block_date, daily_dart, cum_debt]
    * ``subproxy_usds``              DataFrame[block_date, daily_net, cum_balance] — USDS in subproxy
    * ``subproxy_susds_principal``   ditto for sUSDS, in **USDS-equivalent cost basis**
                                     (``shares × entry_pps``), pre-converted by the orchestrator.
    * ``alm_usds``                   ditto for USDS in ALM proxy
    * ``ssr``                        DataFrame[effective_date, ssr_apy] — SP-BEAM changes
    * ``psm_usds``                   optional DataFrame[block_date, daily_net, cum_balance] of USDS
                                     the prime has parked at PSM; subtracted from utilized so the
                                     prime is reimbursed BR on those holdings.
    """
    # Hard-fail on empty debt/ssr — without these, utilized would silently be
    # ≤ 0 every day and sky_revenue would return $0. Loud error pointing at
    # the likely misconfig (ilk_bytes32 / SSR source) beats silent zero.
    require_non_empty(
        debt, name="debt",
        hint="Check `prime.ilk_bytes32` in the YAML and the IDebtSource impl.",
    )
    require_non_empty(
        ssr, name="ssr_history",
        hint="Check the ISSRSource impl — SSR_HISTORY_ANCHOR may be wrong.",
    )

    use_subsidy = subsidy_config is not None and subsidy_config.enabled
    if use_subsidy and ref_rate_history is None:
        raise ValueError(
            "subsidy_config.enabled but no ref_rate_history provided. "
            "Pass a ReferenceRateHistory loaded from "
            "config/subsidy_reference_rates.yaml."
        )

    total = Decimal("0")
    current = period.start
    while current <= period.end:
        cum_debt = cum_at_or_before(debt, "cum_debt", current)
        cum_sub_usds = cum_at_or_before(subproxy_usds, "cum_balance", current)
        cum_sub_susds = cum_at_or_before(subproxy_susds_principal, "cum_balance", current)
        cum_alm_usds = cum_at_or_before(alm_usds, "cum_balance", current)
        # SDE positions (BUIDL, JTRSY, USTB, JAAA-cap, …) — Sky books their
        # actual revenue directly via ``sd_revenue`` in the venue breakdown,
        # so they're excluded from BR base here to avoid double-charging.
        # ``cum_at_or_before`` returns 0 for None / empty inputs.
        cum_psm_usds = cum_at_or_before(psm_usds, "cum_balance", current)
        cum_sde = cum_at_or_before(sde_asset_value, "cum_value", current)

        utilized = cum_debt - cum_sub_usds - cum_sub_susds - cum_alm_usds - cum_psm_usds - cum_sde

        if utilized > 0:
            ssr_apy = ssr_at_or_before(ssr, current)
            base_apy = ssr_apy + BASE_RATE_OVER_SSR
            if use_subsidy:
                # Split utilized: first cap_usd at subsidised, excess at full BR.
                cap = subsidy_config.cap_usd
                subsidised_part = min(utilized, cap)
                excess_part = max(Decimal("0"), utilized - cap)
                ref_rate = ref_rate_history.at(current)
                t = months_elapsed_since(current, subsidy_config.program_start)
                sub_apy = subsidised_apy(
                    base_apy, ref_rate, t, subsidy_config.ramp_months
                )
                total += subsidised_part * daily_compounding_factor(sub_apy)
                if excess_part > 0:
                    total += excess_part * daily_compounding_factor(base_apy)
            else:
                total += utilized * daily_compounding_factor(base_apy)

        current = current + timedelta(days=1)

    return total
