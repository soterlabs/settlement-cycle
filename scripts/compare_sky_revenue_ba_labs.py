"""Compare our reported ``sky_revenue`` against BA Labs' implied numbers.

BA Labs (``observatory.data.blockanalitica.com``) exposes Grove's daily
liability (debt to Sky) via the balance-sheet aggregates endpoint. We
don't have a direct ``sky_revenue`` feed from them — instead we use the
debt timeseries + our subsidy reference rates to compute the implied
"interest if all of debt were utilised at the subsidised BR" per month,
and compare that to what our pipeline actually reported.

Three numbers per month:

  * **BA Labs avg debt** — time-weighted average of daily liability
    snapshots within the month
  * **Implied Sky interest (BA Labs avg debt × BR)** — what Sky would
    earn if all of the reported debt were utilised at the subsidised
    borrowing rate that applied during the month
  * **Our sky_revenue** — the value from
    ``settlements/grove/{month}/pnl.csv``

The third minus the second isolates the methodology delta:

  * Our pipeline charges BR only on *utilised* debt (subtracts ALM idle,
    PSM, SDE asset value, Curve/lending idle reserves).
  * Plus an SDE absorption component that's a separate flow from Sky to
    the prime when prime's yield falls short of BR.

The naive ``debt × BR`` comparison will overstate the BR-on-debt
component (because of idle deductions) but undercount the SDE
absorption. The gap surfaces both methodology differences.
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


def fetch_ba_labs_daily_debt() -> dict[date, Decimal]:
    """Returns ``{date: liability_usd}`` from the daily balance-sheet
    aggregates endpoint."""
    raw = _http_get_json(
        "/primes/grove/balance-sheet/aggregates/?group_by=day",
    )
    out: dict[date, Decimal] = {}
    for r in raw.get("data", []):
        if r.get("what") != "liabilities":
            continue
        d = date.fromisoformat(r["date"])  # YYYY-MM-DD for group_by=day
        out[d] = Decimal(r["balance"])
    return out


def load_subsidy_rates() -> list[tuple[date, Decimal]]:
    """Returns sorted ``[(effective_date, tbill_3m_apy), ...]`` from the
    config (carry-forward applied at lookup time)."""
    path = _REPO / "config" / "subsidy_reference_rates.yaml"
    blob = yaml.safe_load(path.read_text())
    rates: list[tuple[date, Decimal]] = []
    for r in blob["rates"]:
        rates.append((date.fromisoformat(r["effective_date"]),
                      Decimal(str(r["tbill_3m_apy"]))))
    return sorted(rates)


def ref_rate_at(d: date, rates: list[tuple[date, Decimal]]) -> Decimal:
    """Carry-forward lookup: most-recent effective_date <= d."""
    chosen = rates[0][1]
    for eff, r in rates:
        if eff <= d:
            chosen = r
        else:
            break
    return chosen


# Grove uses tbill_3m as the reference rate. The subsidised borrowing
# rate is (per ``config/grove.yaml`` subsidy curve): base_apy − subsidy ×
# (base_apy − ref_rate). Subsidy decays over 24 months from prime start.
# For a first-pass comparison we use the simplest form: BR ≈ tbill_3m +
# 30 bps (matches our ``sky_revenue`` formula's BR = ssr + 0.30%). The
# real curve is more nuanced but Sky's effective rate stays close to
# this for the periods we care about.
_BR_SPREAD = Decimal("0.003")


def implied_sky_interest_for_month(
    year: int, month: int,
    daily_debt: dict[date, Decimal],
    rates: list[tuple[date, Decimal]],
) -> tuple[Decimal, Decimal, int]:
    """Returns ``(avg_debt, implied_interest, n_days)`` for the month.

    avg_debt = arithmetic mean of daily liability snapshots in the month.
    implied_interest = Σ_d (debt_d × BR_d / 365).
    """
    next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    start, end = date(year, month, 1), date(*next_month, 1) - timedelta(days=1)

    debt_sum = Decimal(0)
    interest_sum = Decimal(0)
    n = 0
    cur = start
    last_debt = None
    while cur <= end:
        if cur in daily_debt:
            last_debt = daily_debt[cur]
        if last_debt is None:
            cur += timedelta(days=1)
            continue
        ref = ref_rate_at(cur, rates)
        br = ref + _BR_SPREAD
        debt_sum += last_debt
        interest_sum += last_debt * br / Decimal(365)
        n += 1
        cur += timedelta(days=1)

    if n == 0:
        return Decimal(0), Decimal(0), 0
    return debt_sum / Decimal(n), interest_sum, n


def our_sky_revenue(month_label: str) -> tuple[Decimal, Decimal]:
    """Returns ``(sky_revenue, sky_direct_shortfall)`` from our pnl.csv."""
    path = _REPO / "settlements" / "grove" / month_label / "pnl.csv"
    with path.open() as f:
        row = next(csv.DictReader(f))
    return Decimal(row["sky_revenue"]), Decimal(row["sky_direct_shortfall"])


def main() -> int:
    print("Fetching BA Labs daily debt …")
    daily_debt = fetch_ba_labs_daily_debt()
    print(f"  {len(daily_debt)} daily snapshots: "
          f"{min(daily_debt)} → {max(daily_debt)}")

    rates = load_subsidy_rates()
    print(f"  subsidy reference rates: {len(rates)} effective dates "
          f"({rates[0][0]} → {rates[-1][0]})")
    print()

    print(f"{'Month':<8}{'avg_debt (BA)':>22}{'implied_interest':>22}"
          f"{'our sky_revenue':>20}{'(sky-implied)':>16}{'sde_shortfall':>18}")
    print("-" * 120)

    for (y, m) in [(2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5)]:
        avg_debt, implied, n = implied_sky_interest_for_month(y, m, daily_debt, rates)
        if n == 0:
            continue
        label = f"{y}-{m:02d}"
        sky_rev, sde = our_sky_revenue(label)
        delta = sky_rev - implied
        print(f"{label:<8}{float(avg_debt):>22,.0f}"
              f"{float(implied):>22,.0f}"
              f"{float(sky_rev):>20,.0f}"
              f"{float(delta):>16,.0f}"
              f"{float(sde):>18,.0f}")

    print()
    print("Notes:")
    print(f"  - 'implied_interest' = Σ_d (daily_debt × BR_d / 365)")
    print(f"  - BR_d = tbill_3m(d) + {float(_BR_SPREAD)*10000:.0f}bps (SSR + 30bps approximation)")
    print(f"  - Our sky_revenue charges BR only on UTILISED debt (subtracting")
    print(f"    ALM idle + PSM + SDE asset value + Curve/lending idle). So")
    print(f"    'our sky_revenue' < 'implied_interest' when idle reserves are")
    print(f"    material.")
    print(f"  - sde_shortfall is the Sky Direct Exposure absorption flow —")
    print(f"    when prime can't cover BR on the SDE-classified venues,")
    print(f"    Sky absorbs the gap as additional revenue. Zero here means")
    print(f"    SDE wasn't loss-absorbing this month.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
