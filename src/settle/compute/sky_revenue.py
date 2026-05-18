"""Sky revenue — interest the prime owes Sky on utilized USDS.

Per the prime-settlement-methodology and debt-rate-methodology docs:

    daily_sky_revenue = utilized × [(1 + apy)^(1/365) - 1]
    apy               = base_apy (default) | subsidised_apy (when enabled)
    base_apy          = SSR + 30bps
    subsidised_apy    = ref_rate + (base − ref_rate) × T / 24    [Step 1.b]
    utilized          = cum_debt
                      − alm_proxy_usds                 ←  Step 2 (idle USDS at ALM proxy)
                      − psm_usds                       ←  Step 2 (idle USDS in PSM3)
                      − curve_idle_usds                ←  Step 2 (prime's USDS share in Curve pools)
                      − lending_idle_usds              ←  Step 2 (prime's share of unborrowed underlying in lending pools)

Subproxy USDS and sUSDS are NOT subtracted from utilized. The subproxy holds
a mix of genesis capital, treasury holdings, risk capital, and realized
revenue that does not all correspond to ilk debt — deducting it from utilized
would over-reimburse the prime for capital it did not borrow from Sky.
Subproxy balances earn the agent rate instead (see ``compute_agent_rate``).

sUSDS venues (``sky_savings_token: true`` in the prime YAML config) are also
NOT subtracted from utilized. The prime earns only the 30 bps spread (BR − SSR)
on these positions — the SSR appreciation flows back to Sky via this
borrow-rate charge. The spread is computed in ``compute_monthly_pnl`` and
injected as ``VenueRevenueInputs.actual_revenue_override``; sky_revenue
itself sees the full utilized unchanged.

``curve_idle_usds`` is the prime's proportional USDS held inside Curve pools
configured with a **par-stable** ``curve_idle_usds:`` coin (USDS, USDC, …),
computed daily as::

    prime_usds_d = (alm_lp_balance_d / pool_total_supply_d) × coin_reserve_d

Only par-stable coin reserves are deducted. Venues where the configured coin
is yield-bearing (sUSDS, …) are tracked in the pipeline for future Prime
Revenue use but contribute zero here — converting yield-bearing balances to
USDS and subtracting from utilized is incorrect.

``lending_idle_usds`` is the prime's proportional share of unborrowed underlying
inside configured lending pools (``lending_idle_usds: true`` YAML flag), computed
daily as::

    prime_idle_d = (balanceOf(alm, spToken_d) / totalSupply(spToken_d))
                 × balanceOf(spToken_contract, underlying_d)

where ``spToken`` is the venue's rebasing lending token (spUSDS, spDAI) and the
underlying (USDS, DAI) is a par-stable at $1. This covers unborrowed capital
that hasn't left the pool — the prime is reimbursed BR on this idle portion.

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
* Idle USDS/DAI in non-Curve AMMs (e.g. Uniswap V3) is not yet plumbed.
  Curve coverage is handled via ``curve_idle_usds`` (see above).

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
    combine_apys,
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
    alm_usds: pd.DataFrame,
    ssr: pd.DataFrame,
    psm_usds: pd.DataFrame | None = None,
    *,
    subsidy_config: SubsidyConfig | None = None,
    ref_rate_history: ReferenceRateHistory | None = None,
    sde_asset_value: pd.DataFrame | None = None,
    curve_idle_usds: pd.DataFrame | None = None,
    lending_idle_usds: pd.DataFrame | None = None,
) -> Decimal:
    """Sum of daily Sky revenue over ``period``.  See ``compute_sky_revenue_daily``
    for the full docstring and per-day breakdown."""
    total, _ = compute_sky_revenue_daily(
        period, debt, alm_usds, ssr, psm_usds,
        subsidy_config=subsidy_config,
        ref_rate_history=ref_rate_history,
        sde_asset_value=sde_asset_value,
        curve_idle_usds=curve_idle_usds,
        lending_idle_usds=lending_idle_usds,
    )
    return total


def compute_sky_revenue_daily(
    period: Period,
    debt: pd.DataFrame,
    alm_usds: pd.DataFrame,
    ssr: pd.DataFrame,
    psm_usds: pd.DataFrame | None = None,
    *,
    subsidy_config: SubsidyConfig | None = None,
    ref_rate_history: ReferenceRateHistory | None = None,
    sde_asset_value: pd.DataFrame | None = None,
    curve_idle_usds: pd.DataFrame | None = None,
    lending_idle_usds: pd.DataFrame | None = None,
) -> tuple[Decimal, pd.DataFrame]:
    """Sum of daily Sky revenue over ``period`` plus a full day-by-day breakdown.

    Returns ``(total, daily_df)`` where ``daily_df`` has one row per calendar
    day in the period with columns::

        date            — calendar date
        cum_debt        — gross ilk debt (USDS) at that day's EoD block
        alm_usds        — ALM-proxy idle USDS deducted from debt
        psm_usds        — PSM3 / lite-PSM idle USDS deducted
        sde_av          — SDE asset value deducted (BR not charged; actual
                          revenue charged separately via sd_revenue)
        curve_idle      — Curve pool idle USDS-equivalent deducted
        lending_idle    — lending pool idle USDS deducted
        utilized        — net charged base  (= cum_debt − all deductions above)
        ssr_apy         — Sky Savings Rate APY on that day (float, e.g. 0.1250)
        base_apy        — borrow rate APY  (= ssr_apy + 30bps)
        daily_sky_rev   — BR revenue for that day (Decimal)

    Inputs (all Normalize outputs):

    * ``debt``                       DataFrame[block_date, daily_dart, cum_debt]
    * ``alm_usds``                   DataFrame[block_date, daily_net, cum_balance] — idle USDS at ALM proxy
    * ``ssr``                        DataFrame[effective_date, ssr_apy] — SP-BEAM changes
    * ``psm_usds``                   optional 6-column DataFrame[block_date, daily_net, cum_balance,
                                     cum_usdc, cum_usds_leg, cum_susds] of the prime's PSM holdings.
                                     Per PRD §17.11 the three legs are routed separately:
                                       - USDS leg  → subtracted from ``utilized`` (BR-reimbursed)
                                       - USDC leg  → added to ``sde_asset_value`` (Sky Direct
                                                     Exposure per Atlas §A.2.3.2.2.3)
                                       - sUSDS leg → NOT subtracted here; the orchestrator credits
                                                     the prime ``30 bps × value × n_days`` as Prime
                                                     Revenue so the SSR-via-share-price + BR-charge
                                                     + 30 bps-credit composite nets to zero
                                                     (economic neutrality on idle sUSDS).
    * ``curve_idle_usds``            optional DataFrame[block_date, daily_net, cum_balance] of the
                                     prime's proportional USDS-equivalent inside configured Curve
                                     pools (prime-settlement-methodology Step 2 — AMM idle USDS).
                                     Built daily by the orchestrator via RPC ``read_pool`` +
                                     ``balanceOf`` + ``convertToAssets`` (for sUSDS legs).
                                     ``cum_balance`` is a daily snapshot (not a running total),
                                     matching the PSM3 ERC4626-shares convention.
    * ``lending_idle_usds``          optional DataFrame[block_date, daily_net, cum_balance] of the
                                     prime's proportional share of unborrowed underlying in
                                     configured lending pools (``lending_idle_usds: true``).
                                     Built daily via ``balanceOf`` + ``totalSupply``.

    Subproxy USDS/sUSDS are NOT passed here — they earn the agent rate
    (``compute_agent_rate``) but are not subtracted from utilized because the
    subproxy holds treasury/risk capital beyond pure ilk-debt proceeds.
    """
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

    rows: list[dict] = []
    total = Decimal("0")
    current = period.start
    while current <= period.end:
        cum_debt = cum_at_or_before(debt, "cum_debt", current)
        cum_alm_usds = cum_at_or_before(alm_usds, "cum_balance", current)
        # SDE positions (BUIDL, JTRSY, USTB, JAAA-cap, …) — Sky books their
        # actual revenue directly via ``sd_revenue`` in the venue breakdown,
        # so they're excluded from BR base here to avoid double-charging.
        # ``cum_at_or_before`` returns 0 for None / empty inputs.
        # PSM3 leg-split (PRD §17.11), three different treatments:
        #   - USDS  leg → subtracted from utilized (idle USDS at PSM3, no SSR
        #                 to offset, prime simply doesn't pay BR on this slice)
        #   - USDC  leg → SDE per Atlas §A.2.3.2.2.3 (Sky takes the actual
        #                 yield, ≈ $0 for passive reserves); folded into
        #                 ``cum_sde`` so it's excluded from BR base
        #   - sUSDS leg → NOT subtracted here. The prime captures SSR via the
        #                 share-price appreciation of its PSM3 claim; charging
        #                 full BR on this slice and crediting the prime 30 bps
        #                 as Prime Revenue (in the orchestrator) makes the
        #                 SSR / BR / 30 bps composite net to zero. Subtracting
        #                 here would give the prime SSR for free at Sky's
        #                 expense.
        cum_psm_usds_leg  = cum_at_or_before(psm_usds, "cum_usds_leg", current)
        cum_psm_usdc_sde  = cum_at_or_before(psm_usds, "cum_usdc",     current)
        cum_sde = cum_at_or_before(sde_asset_value, "cum_value", current) + cum_psm_usdc_sde
        # Prime's proportional USDS-equivalent in Curve pools — Step 2 idle AMM.
        cum_curve_usds = cum_at_or_before(curve_idle_usds, "cum_balance", current)
        # Prime's share of unborrowed underlying in lending pools — Step 2 idle lending.
        cum_lending_idle = cum_at_or_before(lending_idle_usds, "cum_balance", current)

        utilized = (
            cum_debt
            - cum_alm_usds
            - cum_psm_usds_leg
            - cum_sde
            - cum_curve_usds
            - cum_lending_idle
        )

        daily_rev = Decimal("0")
        ssr_apy   = Decimal("0")
        base_apy  = Decimal("0")
        if utilized > 0:
            ssr_apy  = ssr_at_or_before(ssr, current)
            # APYs combine multiplicatively, not additively: naive
            # ``ssr_apy + 30bps`` loses the cross-term ``ssr_apy × 30bps``
            # (~1.2 bps at SSR=4%). See ``combine_apys`` in ``_helpers.py``.
            base_apy = combine_apys(ssr_apy, BASE_RATE_OVER_SSR)
            if use_subsidy:
                cap              = subsidy_config.cap_usd
                subsidised_part  = min(utilized, cap)
                excess_part      = max(Decimal("0"), utilized - cap)
                ref_rate         = ref_rate_history.at(current)
                t                = months_elapsed_since(current, subsidy_config.program_start)
                sub_apy          = subsidised_apy(base_apy, ref_rate, t, subsidy_config.ramp_months)
                daily_rev        = subsidised_part * daily_compounding_factor(sub_apy)
                if excess_part > 0:
                    daily_rev += excess_part * daily_compounding_factor(base_apy)
            else:
                daily_rev = utilized * daily_compounding_factor(base_apy)

        total += daily_rev
        rows.append({
            "date":          current,
            "cum_debt":      cum_debt,
            "alm_usds":      cum_alm_usds,
            "psm_usds":      cum_psm_usds_leg,
            "sde_av":        cum_sde,
            "curve_idle":    cum_curve_usds,
            "lending_idle":  cum_lending_idle,
            "utilized":      utilized,
            "ssr_apy":       float(ssr_apy),
            "base_apy":      float(base_apy),
            "daily_sky_rev": daily_rev,
        })
        current = current + timedelta(days=1)

    return total, pd.DataFrame(rows)
