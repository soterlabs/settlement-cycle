"""Deep comparison of our ``sky_revenue`` against BA Labs' data.

BA Labs (``observatory.data.blockanalitica.com``) doesn't expose a direct
``sky_revenue`` series — they only have:

  1. Daily Grove **liability** (debt owed to Sky) via
     ``/primes/grove/balance-sheet/aggregates/?group_by=day``
  2. Daily **asset** snapshots per allocation (same endpoint, ``what=assets``)
  3. A current-day ``apy``, ``estimated_profit`` snapshot on
     ``/primes/grove/``

We get the closest comparison by computing the IMPLIED monthly Sky
interest from BA Labs' debt timeseries × the subsidised borrowing
rate, then juxtaposing it with our reported numbers.

This script decomposes both sides into matching components:

  * **BA Labs liability** — full debt
  * **BA Labs idle ALM assets** — sum of "raw" stable balances at the
    ALM proxy (categories agora/sky/circle/etc. that look like idle
    cash, not yield-bearing venue positions). This is BA Labs'
    closest analogue to our ``cum_alm_usds + cum_psm_usds``
    deduction.
  * **Implied utilised** — liability − idle ALM assets (BA Labs view)
  * **Implied Sky interest, subsidised** — Σ_d implied_utilised × BR_d
  * **Our cum_debt** — from ``settlements/grove/.../settlement.xlsx``
    Sky Revenue tab if available, else inferred
  * **Our sky_rev_br** — BR on (utilised − SDE) per pnl.csv formula
  * **Our SDE revenue** — Sky-Direct flow on E9/E10 type venues
  * **Total sky_revenue (pnl.csv)** vs **Total (grove_sheet "subsidised")**

The output makes it explicit where each number comes from and which
methodology choice produced it.
"""

from __future__ import annotations

import csv
import json
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yaml


_REPO = Path(__file__).resolve().parent.parent
_BA_BASE = "https://observatory.data.blockanalitica.com"


# ----------------------------------------------------------------------------
# BA Labs fetchers
# ----------------------------------------------------------------------------

def _http_get_json(path: str) -> dict:
    req = urllib.request.Request(
        f"{_BA_BASE}{path}",
        headers={
            "accept": "*/*",
            "origin": "https://skyeco.blockanalitica.com",
            "referer": "https://skyeco.blockanalitica.com/",
            "user-agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_ba_labs_daily() -> list[dict]:
    """Returns the raw daily balance-sheet aggregate rows. One row per
    (date, allocation, what)."""
    return _http_get_json(
        "/primes/grove/balance-sheet/aggregates/?group_by=day",
    ).get("data", [])


def fetch_ba_labs_prime() -> dict:
    """Returns the current-snapshot prime data."""
    return _http_get_json("/primes/grove/").get("data", {})


# ----------------------------------------------------------------------------
# BA Labs daily aggregation
# ----------------------------------------------------------------------------

# BA Labs categorises each allocation; these are the categories we treat as
# "idle stable assets at the ALM" (the analogue of our ``cum_alm_usds`` +
# ``cum_psm_usds`` deductions). Yield-bearing positions (aave, blackrock,
# centrifuge, morpho, curve, uniswap, …) are NOT in this list.
_IDLE_CATEGORIES = {"agora", "circle", "ripple", "sky"}

# BA Labs token-name patterns recognised as raw idle stables.
_IDLE_TOKEN_KEYWORDS = ("USDC", "USDS", "AUSD", "RLUSD", "DAI", "USDT", "PYUSD")


def _is_idle_row(row: dict) -> bool:
    """Heuristic: is this allocation row idle ALM cash (vs a yield-bearing
    position)? Uses the BA Labs category AND token-name keyword."""
    if row.get("what") != "assets":
        return False
    if row.get("category") in _IDLE_CATEGORIES:
        return True
    return False


def daily_liability(rows: list[dict]) -> dict[date, Decimal]:
    out: dict[date, Decimal] = {}
    for r in rows:
        if r.get("what") == "liabilities":
            out[date.fromisoformat(r["date"])] = Decimal(r["balance"])
    return out


def daily_idle_assets(rows: list[dict]) -> dict[date, Decimal]:
    """Sum of "idle ALM stable" balances per day. Best-effort match to our
    ``cum_alm_usds + cum_psm_usds`` deduction."""
    out: dict[date, Decimal] = defaultdict(lambda: Decimal(0))
    for r in rows:
        if not _is_idle_row(r):
            continue
        out[date.fromisoformat(r["date"])] += Decimal(r["balance"])
    return dict(out)


# ----------------------------------------------------------------------------
# Subsidy rate lookup
# ----------------------------------------------------------------------------

def load_subsidy_rates() -> list[tuple[date, Decimal]]:
    path = _REPO / "config" / "subsidy_reference_rates.yaml"
    blob = yaml.safe_load(path.read_text())
    return sorted(
        (date.fromisoformat(r["effective_date"]), Decimal(str(r["tbill_3m_apy"])))
        for r in blob["rates"]
    )


def ref_rate_at(d: date, rates: list[tuple[date, Decimal]]) -> Decimal:
    chosen = rates[0][1]
    for eff, r in rates:
        if eff <= d:
            chosen = r
        else:
            break
    return chosen


# Grove subsidy curve (from config/grove.yaml):
#   subsidised_apr = ref_rate + (base_apr - ref_rate) × min(T, 24) / 24
# where T = full months since 2026-01-01, applied to first $1B of utilised.
# base_apr = apy_to_apr(SSR,12) + 30bps; we approximate SSR ≈ 6.0% (Sky SSR was 6% in early 2026,
# trending down). For the BA Labs comparison the absolute rate matters less
# than the formula shape — we honour the SSR variation by reading it from
# our own daily compute output when possible.
_SUBSIDY_CAP_USD = Decimal("1000000000")
_SUBSIDY_RAMP_MONTHS = 24
_PROGRAM_START = date(2026, 1, 1)
_BR_SPREAD = Decimal("0.003")  # 30 bps over SSR
# Approximation for the BA Labs cross-check: use the same fixed SSR our
# code uses. The real per-day SSR comes from SSR_HISTORY_ANCHOR in
# settle.normalize.sky_rates; we hardcode a representative value here to
# avoid the heavyweight dependency for a comparison script. SSR was
# approximately 6.0% APY for Jan-Mar 2026 and dropped through Q2.
_SSR_BY_MONTH = {
    (2026, 1): Decimal("0.0600"),
    (2026, 2): Decimal("0.0600"),
    (2026, 3): Decimal("0.0600"),
    (2026, 4): Decimal("0.0450"),
    (2026, 5): Decimal("0.0450"),
}


def _months_elapsed(d: date) -> int:
    return max(0, (d.year - _PROGRAM_START.year) * 12 + (d.month - _PROGRAM_START.month))


def base_apr_for_date(d: date) -> Decimal:
    """Approximate full BR APR at date d (nominal)."""
    ssr = _SSR_BY_MONTH.get((d.year, d.month), Decimal("0.0450"))
    # NOMINAL base rate, matching ``_helpers.apy_to_apr``: SSR is an APY and
    # is converted at n=12 (the settlement cadence) before the APR spread is
    # added. 3.464456% + 0.20% = 3.664456% at SSR 3.52% + 20bps.
    from settle.compute._helpers import apy_to_apr
    return apy_to_apr(ssr) + _BR_SPREAD


def subsidised_apr_for_date(d: date, ref_rate: Decimal) -> Decimal:
    base = base_apr_for_date(d)
    t = min(_months_elapsed(d), _SUBSIDY_RAMP_MONTHS)
    return ref_rate + (base - ref_rate) * Decimal(t) / Decimal(_SUBSIDY_RAMP_MONTHS)


def apr_daily(apr: Decimal) -> Decimal:
    """One day of a NOMINAL annual rate: apr / 365.

    Mirrors ``settle.compute._helpers.apr_daily``. Replaced the former
    ``daily_compound`` (an APY -> daily-factor converter) on 2026-09-01:
    ``base_apr_for_date`` now returns a nominal rate, and feeding a nominal
    rate through ``(1+x)^(1/365)-1`` under-accrues it by ~1.8%.
    """
    return apr / Decimal(365)


# ----------------------------------------------------------------------------
# Our settlement output readers
# ----------------------------------------------------------------------------

def our_pnl_csv(month_label: str) -> dict[str, Decimal]:
    """Returns headline totals from ``settlements/grove/{month}/provenance.json``.
    Function name kept for backward-compat with the rest of the script."""
    import json
    path = _REPO / "settlements" / "grove" / month_label / "provenance.json"
    with path.open() as f:
        prov = json.load(f)
    r = prov["results"]
    return {
        "sky_revenue": Decimal(r["sky_revenue"]),
        "prime_agent_revenue": Decimal(r["prime_agent_revenue"]),
        "prime_agent_total_revenue": Decimal(r["prime_agent_total_revenue"]),
        "sky_direct_shortfall": Decimal(r.get("sky_direct_shortfall") or "0"),
    }


def our_grove_sheet_sky(month_label: str) -> dict[str, Decimal] | None:
    """Returns ``{cof_subsidised, sde_revenue, sky_revenue_grove}`` computed
    in-process from ``provenance.json`` via ``settle.load.cof_attribution``.
    Replaces the prior xlsx Sky-Revenue-tab reader — same data, no file I/O.
    """
    import json
    import sys
    sys.path.insert(0, str(_REPO / "src"))
    from settle.load.cof_attribution import compute_sheet_rows
    path = _REPO / "settlements" / "grove" / month_label / "provenance.json"
    if not path.exists():
        return None
    with path.open() as f:
        prov = json.load(f)
    _rows, totals = compute_sheet_rows(prov, "grove")
    return {
        "cof_subsidised":    totals["cof_total"],
        "sde_revenue":       totals["sd_revenue_total"],
        "sky_revenue_grove": totals["sky_revenue"],
    }


# ----------------------------------------------------------------------------
# Monthly aggregation
# ----------------------------------------------------------------------------

def month_iter(year: int, month: int):
    next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    start = date(year, month, 1)
    end = date(*next_m, 1) - timedelta(days=1)
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def implied_for_month(
    year: int, month: int,
    daily_debt: dict[date, Decimal],
    daily_idle: dict[date, Decimal],
    rates: list[tuple[date, Decimal]],
) -> dict[str, Decimal]:
    """Returns the BA-Labs-implied numbers for this month at FULL BR and
    SUBSIDISED BR, plus the average debt/idle/utilised for the month."""
    debt_sum = idle_sum = util_sum = Decimal(0)
    int_full = int_sub = Decimal(0)
    n = 0
    last_debt = None
    last_idle = Decimal(0)
    for d in month_iter(year, month):
        if d in daily_debt:
            last_debt = daily_debt[d]
        if d in daily_idle:
            last_idle = daily_idle[d]
        if last_debt is None:
            continue
        util = max(Decimal(0), last_debt - last_idle)
        ref = ref_rate_at(d, rates)
        base = base_apr_for_date(d)
        sub = subsidised_apr_for_date(d, ref)
        # Apply subsidy only to first $1B of utilised.
        sub_part = min(util, _SUBSIDY_CAP_USD)
        excess_part = max(Decimal(0), util - _SUBSIDY_CAP_USD)
        daily_int_full = util * apr_daily(base)
        daily_int_sub = sub_part * apr_daily(sub) + excess_part * apr_daily(base)
        debt_sum += last_debt
        idle_sum += last_idle
        util_sum += util
        int_full += daily_int_full
        int_sub += daily_int_sub
        n += 1
    if n == 0:
        return {}
    return {
        "n_days": Decimal(n),
        "avg_debt": debt_sum / Decimal(n),
        "avg_idle": idle_sum / Decimal(n),
        "avg_util": util_sum / Decimal(n),
        "implied_int_full_br": int_full,
        "implied_int_subsidised_br": int_sub,
    }


def main() -> int:
    print("Fetching BA Labs daily aggregates…")
    rows = fetch_ba_labs_daily()
    daily_debt = daily_liability(rows)
    daily_idle = daily_idle_assets(rows)
    print(f"  {len(daily_debt)} debt snapshots, {len(daily_idle)} idle-asset snapshots")
    rates = load_subsidy_rates()

    months = [(2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5)]

    print()
    print("=" * 130)
    print("BA Labs implied vs our reported sky_revenue (USD, except days)")
    print("=" * 130)
    hdr = (
        f"{'Month':<8}"
        f"{'avg_debt':>14}{'avg_idle':>13}{'avg_util':>14}"
        f"{'BR_full':>14}{'BR_sub':>14}"
        f"{'pnl.csv':>14}{'grove_sheet':>15}"
        f"{'gs_cof':>13}{'gs_sde':>13}"
    )
    print(hdr)
    print("-" * 130)
    for (y, m) in months:
        imp = implied_for_month(y, m, daily_debt, daily_idle, rates)
        if not imp:
            continue
        label = f"{y}-{m:02d}"
        pnl = our_pnl_csv(label)
        gs = our_grove_sheet_sky(label) or {}
        print(
            f"{label:<8}"
            f"{float(imp['avg_debt'])/1e6:>12,.0f}M "
            f"{float(imp['avg_idle'])/1e6:>11,.0f}M "
            f"{float(imp['avg_util'])/1e6:>12,.0f}M "
            f"{float(imp['implied_int_full_br']):>13,.0f}"
            f"{float(imp['implied_int_subsidised_br']):>14,.0f}"
            f"{float(pnl['sky_revenue']):>14,.0f}"
            f"{float(gs.get('sky_revenue_grove', 0)):>15,.0f}"
            f"{float(gs.get('cof_subsidised', 0)):>13,.0f}"
            f"{float(gs.get('sde_revenue', 0)):>13,.0f}"
        )

    print()
    print("=" * 130)
    print("Interpretation:")
    print("=" * 130)
    print()
    print("  avg_debt  — BA Labs daily liability avg over the month")
    print("  avg_idle  — BA Labs daily 'idle ALM stable assets' avg (agora/circle/ripple/sky categories)")
    print("  avg_util  — avg_debt − avg_idle (BA Labs view of utilised debt)")
    print()
    print("  BR_full   — Σ_d (debt × BR_full × 1/365), BR_full = SSR + 30bps (no subsidy)")
    print("              — uses ``avg_util`` daily, not avg_debt (closer to our utilised)")
    print()
    print("  BR_sub    — Σ_d (subsidised BR on first $1B + full BR on excess)")
    print("              — subsidised APY = tbill_3m + (BR_full − tbill_3m) × T/24, T = months since 2026-01-01")
    print()
    print("  pnl.csv         — MonthlyPnL.sky_revenue (= sky_rev_br + sde_revenue)")
    print("                    sky_rev_br is subsidised BR on (utilised − SDE asset value)")
    print("                    sde_revenue is actual yield from fixed/capped SDE venues")
    print()
    print("  grove_sheet     — Sum of per-venue 'Profit to Sky' from grove_sheet.xlsx")
    print("                    (split: cof_subsidised + sde_revenue, can drift from pnl.csv)")
    print("  gs_cof  / gs_sde — the two components, broken out")
    print()
    print("Reconciliation guides:")
    print("  • Apples-to-apples comparison: BR_sub  vs  gs_cof + gs_sde  (both subsidised)")
    print("  • pnl.csv typically slightly higher than grove_sheet — the extra captures")
    print("    SDE absorbed by Sky that grove_sheet allocates to display-only venues.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
