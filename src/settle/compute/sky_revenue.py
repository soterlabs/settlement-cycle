"""Sky revenue — interest the prime owes Sky on utilized USDS.

Per the prime-settlement-methodology and debt-rate-methodology docs:

    daily_sky_revenue = utilized × [(1 + apy)^(1/365) - 1]
    apy               = base_apy (default) | subsidised_apy (when enabled)
    base_apy          = SSR ⊕ spread   (30bps; 20bps from 2026-07-23 —
                        see BASE_RATE_SPREAD_SCHEDULE)
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
NOT subtracted from utilized. The prime earns only the BR − SSR spread
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
* ``ref_rate_history`` provides the daily reference rate (3M T-Bill through
  2026-07-22; SOFR from 2026-07-23 via the dated ``ref_rate_kind`` schedule).

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

import logging
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

from ..domain.period import Period
from ..domain.subsidy import (
    ReferenceRateHistory,
    ScheduledReferenceRateHistory,
    SubsidyConfig,
    months_elapsed_since,
    subsidised_apy,
)
from ._helpers import (
    CompoundingAccrual,
    combine_apys,
    cum_at_or_before,
    daily_compounding_factor,
    require_non_empty,
    ssr_at_or_before,
)

_log = logging.getLogger(__name__)

# Spread Sky charges over SSR for utilized debt. Per prime-settlement-
# methodology §1 + debt-rate-methodology, the base rate = SSR ⊕ spread.
#
# The spread is DATED: the 2026-07-23 Stability Scope change that cut the
# SSR 3.60% → 3.52% (on-chain sUSDS ``file("ssr")`` at 2026-07-23 14:43:23
# UTC) also narrowed the BR − SSR spread from 30 bps to 20 bps. Same
# day-granularity carry-forward convention as the SSR series itself
# (``ssr_history.sql`` keeps the last call per UTC day), so the WHOLE of
# 2026-07-23 is charged at the new spread.
BASE_RATE_SPREAD_SCHEDULE: tuple[tuple[date, Decimal], ...] = (
    (date(2024, 9, 1), Decimal("0.003")),   # inception (SSR_HISTORY_ANCHOR)
    (date(2026, 7, 23), Decimal("0.002")),  # SSR 3.52% vote, spread 30→20bps
)

# Pre-2026-07-23 value, kept ONLY for tests that reconstruct expected values
# for earlier periods. Production code must use ``base_rate_spread_at``.
BASE_RATE_OVER_SSR = BASE_RATE_SPREAD_SCHEDULE[0][1]


def base_rate_spread_at(target: date) -> Decimal:
    """BR − SSR spread in effect on ``target`` (carry-forward, like SSR)."""
    spread = BASE_RATE_SPREAD_SCHEDULE[0][1]
    for effective, value in BASE_RATE_SPREAD_SCHEDULE:
        if effective <= target:
            spread = value
        else:
            break
    return spread


def compute_sky_revenue(
    period: Period,
    debt: pd.DataFrame,
    alm_usds: pd.DataFrame,
    ssr: pd.DataFrame,
    psm_usds: pd.DataFrame | None = None,
    *,
    subsidy_config: SubsidyConfig | None = None,
    ref_rate_history: ReferenceRateHistory | ScheduledReferenceRateHistory | None = None,
    sde_asset_value: pd.DataFrame | None = None,
    curve_idle_usds: pd.DataFrame | None = None,
    lending_idle_usds: pd.DataFrame | None = None,
) -> Decimal:
    """Sum of daily Sky revenue over ``period``.  See ``compute_sky_revenue_daily``
    for the full docstring and per-day breakdown."""
    total, _, _ = compute_sky_revenue_daily(
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
    ref_rate_history: ReferenceRateHistory | ScheduledReferenceRateHistory | None = None,
    sde_asset_value: pd.DataFrame | None = None,
    curve_idle_usds: pd.DataFrame | None = None,
    lending_idle_usds: pd.DataFrame | None = None,
) -> tuple[Decimal, pd.DataFrame, dict | None]:
    """Sum of daily Sky revenue over ``period`` plus a full day-by-day breakdown.

    Returns ``(total, daily_df, subsidy_summary)`` where ``daily_df`` has one
    row per calendar day in the period, and ``subsidy_summary`` is the
    per-period subsidy aggregate dict (``None`` when no subsidy is enabled) —
    computed once here and reused both for the zero-benefit warning below and
    by the orchestrator for ``provenance.json``, so there is a single source.
    ``daily_df`` columns::

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
    # Accrued interest compounds — day d is charged on principal + interest
    # accrued on days < d (operator decision 2026-08-24; see
    # ``CompoundingAccrual``). Actual and gross accrue independently.
    accrual = CompoundingAccrual()
    accrual_gross = CompoundingAccrual()
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
        #   - sUSDS leg → NOT subtracted here. Sky charges full BR on this
        #                 slice; the orchestrator deducts the 30 bps spread
        #                 (psm3_susds_spread) from sky_revenue after the fact
        #                 (same treatment as Cat B ALM venues, Rule 5).
        #                 Subtracting here instead would give the prime SSR for
        #                 free at Sky's expense.
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

        # Always compute the rate — needed for both actual (utilized) and
        # gross (cum_debt) revenue.  When cum_debt is 0 both will be 0.
        ssr_apy  = ssr_at_or_before(ssr, current)
        # APYs combine multiplicatively, not additively: naive
        # ``ssr_apy + spread`` loses the cross-term ``ssr_apy × spread``
        # (~1.2 bps at SSR=4%). See ``combine_apys`` in ``_helpers.py``.
        # Spread is date-resolved (30bps → 20bps on 2026-07-23).
        base_apy = combine_apys(ssr_apy, base_rate_spread_at(current))

        # Subsidy params — computed once and reused for both actual + gross.
        _sub_apy: Decimal | None = None
        _ref_rate: Decimal | None = None
        _t: int | None = None
        if use_subsidy and current >= subsidy_config.program_start:  # type: ignore[union-attr]
            _ref_rate = ref_rate_history.at(current)                  # type: ignore[union-attr]
            _t        = months_elapsed_since(current, subsidy_config.program_start)  # type: ignore[union-attr]
            _sub_apy  = subsidised_apy(base_apy, _ref_rate, _t, subsidy_config.ramp_months)  # type: ignore[union-attr]

        def _daily_factor_for(principal: Decimal) -> Decimal:
            """Blended one-day factor on ``principal`` — the subsidy cap
            splits the PRINCIPAL across two rates, so the effective factor
            is the tranche-weighted mean. Returning a factor (rather than a
            dollar amount) lets the compounding accumulator charge the same
            rate on previously accrued interest."""
            if principal <= 0:
                return Decimal("0")
            if _sub_apy is not None:
                cap     = subsidy_config.cap_usd  # type: ignore[union-attr]
                sub_p   = min(principal, cap)
                exc_p   = max(Decimal("0"), principal - cap)
                rev     = sub_p * daily_compounding_factor(_sub_apy)
                if exc_p > 0:
                    rev += exc_p * daily_compounding_factor(base_apy)
                return rev / principal
            return daily_compounding_factor(base_apy)

        # ``add`` charges (principal + accrued) × factor and returns the day's
        # increment, so the per-day rows below still sum to the total.
        daily_rev       = accrual.add(utilized, _daily_factor_for(utilized))
        # Gross: BR on the full ilk debt before any utilized deductions.
        # Captures "what sky_revenue would be if no idle USDS / SDE / PSM /
        # Curve / lending deductions were applied."  Stored per-day so the
        # orchestrator can sum it and write sky_revenue_gross to provenance.
        daily_rev_gross = accrual_gross.add(cum_debt, _daily_factor_for(cum_debt))
        rows.append({
            "date":               current,
            "cum_debt":           cum_debt,
            "alm_usds":           cum_alm_usds,
            "psm_usds":           cum_psm_usds_leg,
            "sde_av":             cum_sde,
            "curve_idle":         cum_curve_usds,
            "lending_idle":       cum_lending_idle,
            "utilized":           utilized,
            "ssr_apy":            float(ssr_apy),
            "base_apy":           float(base_apy),
            # Subsidy ramp position + reference rate + effective subsidised
            # APY. Populated only when ``subsidy_config.enabled`` and
            # ``current >= subsidy.program_start`` — None otherwise so the
            # xlsx "Debt" tab can omit the columns cleanly for non-subsidy
            # primes / pre-program days.
            "ref_rate_apy":       float(_ref_rate) if _ref_rate is not None else None,
            "sub_apy":            float(_sub_apy)  if _sub_apy  is not None else None,
            "t_months":           _t,
            "daily_sky_rev":      daily_rev,
            "daily_sky_rev_gross": daily_rev_gross,
        })
        current = current + timedelta(days=1)

    daily_df = pd.DataFrame(rows)

    # Per-period subsidy aggregates — computed ONCE here and returned, so the
    # zero-benefit warning below and the orchestrator's provenance block share
    # one computation (no double call).
    subsidy_summary = summarize_subsidy(daily_df, subsidy_config)

    # $0-subsidy smell check — driven by the SAME aggregation the report
    # consumes, so the warning and the spreadsheet's zero-benefit flag can
    # never disagree. When the subsidy is enabled but the reference rate sits
    # at/above base_apy on every active day, the ramp clamps to base and the
    # prime gets $0 benefit. Occasionally legitimate (a genuinely high
    # T-Bill — the June 2026 3.87% print is within 9bps of base), but also
    # the exact signature of a stale/placeholder reference rate: the May 2026
    # Spark run carried a January rate of 4.33% (> BR) all month, silently
    # zeroing a ~$0.2M subsidy. The date-staleness
    # guard in ReferenceRateHistory.at() can't catch it (rows present, just
    # wrong), so flag the zero-benefit outcome directly.
    if subsidy_summary is not None and subsidy_summary["zero_benefit"]:
        _log.warning(
            "Subsidy enabled but produced $0 benefit for the whole period "
            "(ref_rate ≥ base_apy every day; ref %.4f%% ≥ base %.4f%%). "
            "Verify the %s reference rate is current — a stale/placeholder "
            "value above the base rate silently nullifies the subsidy "
            "(May 2026 Spark root cause).",
            (subsidy_summary["ref_apy_avg"] or 0) * 100,
            subsidy_summary["base_apy_avg"] * 100,
            subsidy_config.ref_rate_kind,  # type: ignore[union-attr]
        )

    return accrual.total, daily_df, subsidy_summary


def summarize_subsidy(
    daily: pd.DataFrame,
    subsidy_config: SubsidyConfig | None,
) -> dict | None:
    """Per-period subsidy aggregates for the settlement report.

    Single source of truth for the subsidy numbers the spreadsheet renders —
    the "Rates & subsidy" panel only formats this dict, so the report can
    never drift from the rate schedule actually charged in
    ``compute_sky_revenue_daily``. Every CoF figure here is recomputed with
    the same ``daily_compounding_factor`` (and the same cap-tranche split as
    ``_daily_rev_for``) that produced ``daily_sky_rev``, so
    ``sub_tranche_cof + exc_tranche_cof == actual_cof == Σ daily_sky_rev`` to
    the cent and ``subsidy_benefit == full_br_cof − actual_cof``.

    Returns ``None`` when the prime has no subsidy enabled or there are no
    days. All Decimals are stringified and rates are floats so the result is
    JSON-ready for ``provenance.json``.
    """
    if subsidy_config is None or not subsidy_config.enabled:
        return None
    if daily is None or len(daily) == 0:
        return None

    cap = Decimal(str(subsidy_config.cap_usd))
    n = len(daily)
    util_sum = sub_tr = exc_tr = Decimal("0")
    wbase_num = weff_num = Decimal("0")
    actual_cof = sub_cof = exc_cof = Decimal("0")
    # The no-subsidy counterfactual must compound on the same basis as the
    # actual charge, else ``subsidy_benefit`` would absorb the compounding
    # difference instead of measuring only the rate discount.
    full_br = CompoundingAccrual()
    ref_sum = sub_sum = Decimal("0")
    active = 0
    any_benefit_day = False

    for _, r in daily.iterrows():
        u = max(Decimal("0"), Decimal(str(r["utilized"])))
        base = Decimal(str(r["base_apy"]))
        # On pre-program days sub_apy is absent. In a DataFrame that mixes
        # absent and present days pandas coerces the column to float64 and
        # the absent entries become NaN (not None) — so guard with pd.isna,
        # not just ``is not None``; ``Decimal(str(nan)) < base`` would raise
        # InvalidOperation and abort the whole settlement.
        sub_raw = r["sub_apy"]
        sub_present = sub_raw is not None and not pd.isna(sub_raw)
        sub = Decimal(str(sub_raw)) if sub_present else base
        st, ex = min(u, cap), max(Decimal("0"), u - cap)
        base_f = daily_compounding_factor(base)

        util_sum += u
        sub_tr += st
        exc_tr += ex
        wbase_num += u * base
        weff_num += st * sub + ex * base
        # actual_cof reuses the per-day charge the daily loop already
        # computed so the reconciliation
        # ``sub_tranche_cof + exc_tranche_cof == actual_cof == Σ daily_sky_rev``
        # holds by construction, not by a duplicated formula.
        charged = Decimal(str(r["daily_sky_rev"]))
        actual_cof += charged
        # Since 2026-08-24 the charge COMPOUNDS (accrued interest earns), so
        # the day's amount exceeds the tranche-split simple figures. Allocate
        # it pro-rata by each tranche's simple share — the accrued interest
        # is charged at the blended rate, so it splits in the same
        # proportions and the identity above survives to the cent.
        simple_sub = st * daily_compounding_factor(sub)
        simple_exc = ex * base_f
        simple_tot = simple_sub + simple_exc
        if simple_tot > 0:
            sub_cof += charged * simple_sub / simple_tot
            exc_cof += charged * simple_exc / simple_tot
        full_br.add(u, base_f)
        ref_raw = r["ref_rate_apy"]
        if sub_present and ref_raw is not None and not pd.isna(ref_raw):
            ref_sum += Decimal(str(ref_raw))
            sub_sum += sub
            active += 1
            if sub < base:
                any_benefit_day = True

    wbase = (wbase_num / util_sum) if util_sum else Decimal("0")
    eff = (weff_num / util_sum) if util_sum else Decimal("0")
    full_br_cof = full_br.total
    benefit = full_br_cof - actual_cof
    return {
        "cap_usd":              str(cap),
        "ref_rate_kind":        subsidy_config.ref_rate_kind,
        "n_days":               n,
        "tw_utilized":          str(util_sum / n),
        "sub_tranche_balance":  str(sub_tr / n),
        "exc_tranche_balance":  str(exc_tr / n),
        "base_apy_avg":         float(wbase),
        "ref_apy_avg":          float(ref_sum / active) if active else None,
        "sub_apy_avg":          float(sub_sum / active) if active else None,
        "effective_apy":        float(eff),
        "diff_bps":             float((eff - wbase) * Decimal("10000")),
        "actual_cof":           str(actual_cof),
        "full_br_cof":          str(full_br_cof),
        "subsidy_benefit":      str(benefit),
        "sub_tranche_cof":      str(sub_cof),
        "exc_tranche_cof":      str(exc_cof),
        # Only meaningful once the subsidy is active: a period entirely
        # before program_start has active==0 and is "no subsidy yet", not
        # "$0 benefit from a stale rate" — don't flag it.
        "zero_benefit":         active > 0 and not any_benefit_day,
    }
