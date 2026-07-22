#!/usr/bin/env python
"""Diff the Dune and HyperSync non_msc extractors for a month — the side-by-side
parity check (same idiom as scripts/compare_debt_sources.py).

Runs ``compute_non_msc_monthly`` twice on the SAME pin block — once with the
default Dune source, once with ``HyperSyncNonMscSource`` — and prints a per-field
diff. Exit 0 when every P&L-affecting line agrees within the tolerance; exit 1
on any drift. Retire the Dune query only once this is clean across months.

Usage:  python scripts/compare_non_msc_sources.py 2026-06 [--tol 1.0]
Requires DUNE_API_KEY + ENVIO_API_TOKEN (+ RPC/HyperSync for the pin block).
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from settle.compute.non_msc import compute_non_msc_monthly, resolve_pin_block  # noqa: E402
from settle.domain import Month  # noqa: E402
from settle.normalize.sources.hypersync_non_msc import HyperSyncNonMscSource  # noqa: E402

# The prime-held sUSDS carve-out is an INFORMATIONAL split (does not move the
# net); the HyperSync source omits it, so it is not part of the parity gate.
_FIELDS = [
    "stability_fee_income", "psm_jar_income", "liq_owe", "liq_due", "liq_revenue",
    "surplus_return_income", "rwa_jar_void", "susds_expense_gross", "stusds_expense",
    "dsr_expense", "liq_expense", "vest_expense", "total_income", "total_expense",
    "net_revenue",
]


def _val(r, f) -> Decimal:
    return Decimal(getattr(r, f))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("month", help="YYYY-MM")
    ap.add_argument("--tol", type=float, default=1.0, help="abs USDS tolerance per line")
    args = ap.parse_args()
    y, m = (int(x) for x in args.month.split("-"))
    month = Month(y, m)
    pin = resolve_pin_block(month)
    print(f"non_msc source parity — {args.month} @ pin {pin}\n")

    dune = compute_non_msc_monthly(month, pin_block=pin)
    hsx = compute_non_msc_monthly(month, pin_block=pin, source=HyperSyncNonMscSource())

    tol = Decimal(str(args.tol))
    print(f"{'field':<24}{'dune':>18}{'hypersync':>18}{'Δ':>14}")
    print("-" * 74)
    worst = Decimal(0)
    for f in _FIELDS:
        a, b = _val(dune, f), _val(hsx, f)
        d = b - a
        worst = max(worst, abs(d))
        flag = "" if abs(d) <= tol else "  ✗"
        print(f"{f:<24}{float(a):>18,.2f}{float(b):>18,.2f}{float(d):>14,.2f}{flag}")

    # Per-ilk fee diff (the most error-prone line).
    ilks = set(dune.stability_fees_by_ilk) | set(hsx.stability_fees_by_ilk)
    ilk_bad = []
    for ilk in sorted(ilks):
        a = dune.stability_fees_by_ilk.get(ilk, Decimal(0))
        b = hsx.stability_fees_by_ilk.get(ilk, Decimal(0))
        if abs(a - b) > tol:
            ilk_bad.append((ilk, a, b))
    if ilk_bad:
        print("\nper-ilk fee drift:")
        for ilk, a, b in ilk_bad:
            print(f"  {ilk:<14}{float(a):>16,.2f}{float(b):>16,.2f}")

    ok = worst <= tol and not ilk_bad
    print("\n" + ("PARITY OK" if ok else f"DRIFT — worst line Δ {float(worst):,.2f}"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
