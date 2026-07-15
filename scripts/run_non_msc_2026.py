#!/usr/bin/env python
"""Generate the non_msc (Sky protocol P&L outside MSC) reports for 2026 Jan–Jun.

One Dune execution per month (queries/non_msc_streams.sql), artifacts under
settlements/non_msc/<YYYY-MM>/. Requires DUNE_API_KEY (+ RPC for pin blocks).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from settle.compute.non_msc import compute_non_msc_monthly, write_non_msc  # noqa: E402
from settle.domain import Month  # noqa: E402

_MONTHS = [Month(2026, m) for m in range(1, 7)]


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if not os.environ.get("DUNE_API_KEY"):
        print("Missing DUNE_API_KEY (hint: `set -a; source .env; set +a`).")
        return 1

    print("NON_MSC 2026 (Jan → Jun) — Sky protocol P&L outside MSC")
    print("=" * 88)
    print(f"{'Month':<10} {'income':>16} {'expense':>16} {'net_revenue':>16}")
    print("-" * 88)
    failures = 0
    for month in _MONTHS:
        label = f"{month.year}-{month.month:02d}"
        try:
            r = compute_non_msc_monthly(month)
            write_non_msc(r, _REPO / "settlements" / "non_msc" / label)
            flag = "  ⚠" if r.warnings else ""
            print(f"{label:<10} {float(r.total_income):>16,.2f} "
                  f"{float(r.total_expense):>16,.2f} "
                  f"{float(r.net_revenue):>16,.2f}{flag}")
        except Exception as exc:  # keep going; report at the end
            failures += 1
            print(f"{label:<10} FAILED: {exc}")
    print("-" * 88)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
