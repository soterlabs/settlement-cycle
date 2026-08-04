#!/usr/bin/env python
"""Generate the sky_total (Sky Net Revenue, buffer basis) reports for 2026 Jan–Jun.

Implements the 2026-07-16 handoff methodology §3:

    MSC net (buffer basis) = Σ debt minted to buffer per prime
                           − Σ sent to prime subproxies
                           − sent to Demand-side Buffer
                           − sent to Core Council Buffer Multisig
                           − Grove token-launch penalty (excluded from Sky revenue)
    Sky Net Revenue        = MSC net + non-MSC income − non-MSC expense

Extraction is on-chain via HyperSync (no Dune quota dependency). Requires
``ENVIO_API_TOKEN`` (+ RPC for the pin block); ``DATABASE_URL`` optional for
the reorg-safe log store. Reads ``settlements/non_msc/<YYYY-MM>/provenance.json``
for the non-MSC leg — run ``scripts/run_non_msc_2026.py`` first.

Artifacts land under ``settlements/sky_total/<YYYY-MM>/{summary.md,provenance.json}``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from settle.compute.sky_total import (  # noqa: E402
    compute_sky_total_monthly,
    write_sky_total,
)
from settle.domain import Month  # noqa: E402
from settle.normalize.sources.hypersync_msc_buffer import HyperSyncMscBufferSource  # noqa: E402

_MONTHS = [Month(2026, m) for m in range(1, 8)]


def _selected_months() -> list[Month]:
    """``--months 2026-07[,2026-06]`` narrows the run; default = all.
    Loud on bad/missing/zero-match values — see scripts/_months_arg.py."""
    from _months_arg import filter_by_months
    return filter_by_months(_MONTHS, lambda m: (m.year, m.month))


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if not os.environ.get("ENVIO_API_TOKEN"):
        print("Missing ENVIO_API_TOKEN (hint: `set -a; source .env; set +a`).")
        return 1

    source = HyperSyncMscBufferSource()
    print("SKY_TOTAL 2026 — Sky Net Revenue, buffer basis (HyperSync)")
    print("=" * 100)
    print(f"{'Month':<10} {'MSC net':>16} {'non-MSC net':>16} {'Sky Net Revenue':>18}")
    print("-" * 100)
    failures = 0
    for month in _selected_months():
        label = f"{month.year}-{month.month:02d}"
        try:
            r = compute_sky_total_monthly(month, source=source, repo_root=_REPO)
            write_sky_total(r, _REPO / "settlements" / "sky_total" / label)
            flag = "  ⚠" if r.warnings else ""
            print(f"{label:<10} {float(r.msc_net):>16,.2f} "
                  f"{float(r.non_msc_net):>16,.2f} "
                  f"{float(r.sky_net_revenue):>18,.2f}{flag}")
        except Exception as exc:  # keep going; report at the end
            failures += 1
            print(f"{label:<10} FAILED: {exc}")
    print("-" * 100)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
