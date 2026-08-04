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
    print("SKY_TOTAL 2026 — Sky Net Revenue (buffer basis through 2026-06; accrual basis from 2026-07)")
    print("=" * 100)
    print(f"{'Month':<10} {'MSC net':>16} {'non-MSC net':>16} {'Sky Net Revenue':>18}")
    print("-" * 100)
    failures = 0
    # Basis switch: months >= accrual_from (config/sky_total.yaml) are built
    # from the repo's own per-prime artifacts; earlier months stay on the
    # buffer basis (anchored on the M+1 MSC settlement tx).
    from settle.compute.sky_total_accrual import (
        compute_sky_total_accrual,
        write_sky_total_accrual,
    )
    from settle.normalize.sources.hypersync_msc_buffer import load_config
    cfg = load_config()
    import yaml as _yaml
    raw_cfg = _yaml.safe_load((_REPO / "config" / "sky_total.yaml").read_text())
    acc_from = str(raw_cfg.get("accrual_from") or "9999-12")
    acc_y, acc_m = (int(x) for x in acc_from.split("-"))

    for month in _selected_months():
        label = f"{month.year}-{month.month:02d}"
        try:
            if (month.year, month.month) >= (acc_y, acc_m):
                r = compute_sky_total_accrual(month, repo_root=_REPO, config=raw_cfg)
                write_sky_total_accrual(r, _REPO / "settlements" / "sky_total" / label)
            else:
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
