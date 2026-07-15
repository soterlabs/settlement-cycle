#!/usr/bin/env python
"""Consolidated Sky total net revenue — Σ prime sky revenue + non_msc net.

Pure aggregation of already-generated artifacts (no Dune / no RPC): reads
``settlements/<prime>/<YYYY-MM>/provenance.json`` for the five primes and
``settlements/non_msc/<YYYY-MM>/provenance.json``, writes
``settlements/sky_total/<YYYY-MM>/{provenance.json,summary.md}``.

Two headline lines per month:
  * ``sky total net revenue``  = Σ prime supply-side sky revenue + non-MSC net
    (the requested definition — what Sky earns from the prime book plus the
    protocol P&L outside it);
  * ``… net of prime demand-side payments`` = the above MINUS the agent rate
    and Distribution Rewards Sky pays TO the primes — the stricter "all
    Sky-side cash flows" view.

The BA Labs series (info-sky.blockanalitica.com/financials/settlements/
historic/) is rendered as a REFERENCE column when reachable — never blended
into our numbers.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PRIMES = ["spark", "grove", "obex", "keel", "skybase"]
_MONTHS = [f"2026-{m:02d}" for m in range(1, 7)]


def _D(x) -> Decimal:
    return Decimal(str(x)) if x not in (None, "") else Decimal(0)


def _usds(d: Decimal) -> str:
    if d.quantize(Decimal("0.01")) == 0:
        return "0.00"
    return f"-{-d:,.2f}" if d < 0 else f"{d:,.2f}"


def _load_results(unit: str, month: str) -> dict | None:
    p = _REPO / "settlements" / unit / month / "provenance.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())["results"]


def _ba_reference() -> dict[str, Decimal]:
    """Monthly BA Labs net_revenue keyed by YYYY-MM. Reference only; empty on
    any network failure (the report renders 'n/a')."""
    try:
        import requests
        resp = requests.get(
            "https://info-sky.blockanalitica.com/financials/settlements/historic/",
            headers={"accept": "*/*", "origin": "https://info.skyeco.com",
                     "referer": "https://info.skyeco.com/"},
            timeout=15,
        )
        resp.raise_for_status()
        out: dict[str, Decimal] = {}
        for row in resp.json()["data"]:
            start, end = row["reporting_start_date"][:7], row["reporting_end_date"][:7]
            if start == end:                     # single-month cycles only
                out[start] = Decimal(row["net_revenue"])
        return out
    except Exception:
        return {}


def build_month(month: str, ba_ref: dict[str, Decimal]) -> dict | None:
    non_msc = _load_results("non_msc", month)
    if non_msc is None:
        print(f"{month}: missing non_msc provenance — run scripts/run_non_msc_2026.py first")
        return None

    primes: dict[str, dict[str, Decimal]] = {}
    for prime in _PRIMES:
        r = _load_results(prime, month)
        if r is None:
            print(f"{month}: missing {prime} provenance — skipping month")
            return None
        primes[prime] = {
            "sky_revenue": _D(r.get("sky_revenue")),
            "agent_rate": _D(r.get("agent_rate")),
            "distribution_rewards": _D(r.get("distribution_rewards")),
        }

    prime_sky = sum((p["sky_revenue"] for p in primes.values()), Decimal(0))
    demand_side = sum(
        (p["agent_rate"] + p["distribution_rewards"] for p in primes.values()),
        Decimal(0),
    )
    non_msc_net = _D(non_msc["net_revenue"])
    total = prime_sky + non_msc_net
    total_net_of_demand = total - demand_side

    out_dir = _REPO / "settlements" / "sky_total" / month
    out_dir.mkdir(parents=True, exist_ok=True)

    prov = {
        "id": "sky_total",
        "month": month,
        "inputs": {
            "primes": {k: {f: str(v) for f, v in p.items()} for k, p in primes.items()},
            "non_msc_net_revenue": str(non_msc_net),
            "non_msc_warnings": json.loads(
                (_REPO / "settlements" / "non_msc" / month / "provenance.json").read_text()
            ).get("warnings", []),
        },
        "results": {
            "prime_sky_revenue_total": str(prime_sky),
            "non_msc_net_revenue": str(non_msc_net),
            "sky_total_net_revenue": str(total),
            "prime_demand_side_payments": str(demand_side),
            "sky_total_net_of_demand_side": str(total_net_of_demand),
        },
    }
    (out_dir / "provenance.json").write_text(json.dumps(prov, indent=2) + "\n")

    L = [f"# SKY_TOTAL — {month}", ""]
    L.append("Consolidated Sky net revenue: supply-side sky revenue from the "
             "five prime agents (MSC) plus the non-MSC protocol P&L.")
    L.append("")
    L.append("| Component | USDS |")
    L.append("|---|---:|")
    for prime in _PRIMES:
        L.append(f"| sky revenue — {prime} | {_usds(primes[prime]['sky_revenue'])} |")
    L.append(f"| Σ prime sky revenue | {_usds(prime_sky)} |")
    L.append(f"| non-MSC net revenue | {_usds(non_msc_net)} |")
    L.append(f"| **sky total net revenue** | **{_usds(total)}** |")
    L.append("")
    L.append("Demand-side payments Sky makes TO the primes (agent rate + "
             "Distribution Rewards) are not part of the definition above; the "
             "stricter all-flows view nets them out:")
    L.append("")
    L.append("| Field | USDS |")
    L.append("|---|---:|")
    L.append(f"| less: prime demand-side payments (agent rate + DR) | -{_usds(demand_side)} |")
    L.append(f"| **sky total net of demand-side payments** | **{_usds(total_net_of_demand)}** |")
    L.append("")
    ref = ba_ref.get(month)
    L.append(f"> Reference (BA Labs `financials/settlements/historic`, not "
             f"blended): net_revenue = "
             f"{_usds(ref) if ref is not None else 'n/a'}")
    warns = prov["inputs"]["non_msc_warnings"]
    for w in warns:
        L.append(f"> ⚠ non_msc: {w}")
    L.append("")
    (out_dir / "summary.md").write_text("\n".join(L))

    return {
        "month": month, "prime_sky": prime_sky, "non_msc": non_msc_net,
        "total": total, "net_of_demand": total_net_of_demand, "ba": ref,
    }


def main() -> int:
    ba_ref = _ba_reference()
    print("SKY_TOTAL 2026 (Jan → Jun)")
    print("=" * 100)
    print(f"{'Month':<9} {'Σ prime sky':>15} {'non-MSC net':>15} {'sky total':>15} "
          f"{'net of demand':>15} {'BA ref':>15}")
    print("-" * 100)
    failures = 0
    for month in _MONTHS:
        row = build_month(month, ba_ref)
        if row is None:
            failures += 1
            continue
        ba = f"{float(row['ba']):>15,.2f}" if row["ba"] is not None else f"{'n/a':>15}"
        print(f"{row['month']:<9} {float(row['prime_sky']):>15,.2f} "
              f"{float(row['non_msc']):>15,.2f} {float(row['total']):>15,.2f} "
              f"{float(row['net_of_demand']):>15,.2f} {ba}")
    print("-" * 100)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
