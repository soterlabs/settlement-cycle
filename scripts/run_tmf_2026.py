#!/usr/bin/env python
"""Generate the Treasury Management Function (TMF) reports for 2026.

Per month: the Atlas A.2.3 waterfall (SNR → Step 1 → Step 2 → Step 3 engine
budget → Step 4 staking rewards), the Smart Burn Engine's actual execution
(kicks, USDS spent, SKY bought, burn attribution) from on-chain events, and
the on-chain parameter state at month-end — cross-checked against the
executive's published parameter block pinned in ``config/tmf.yaml``.

Inputs
  config/tmf.yaml                 policy, contracts, per-month pinned inputs
  settlements/sky_total/<month>   SNR cross-check (optional)
  ENVIO_API_TOKEN                 HyperSync — kicks / File / Distribute events
  ETH_RPC                         eth_call state reads (cached)

Artifacts land under ``settlements/tmf/<YYYY-MM>/{summary.md,provenance.json}``.

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python3 scripts/run_tmf_2026.py [--months 2026-07,2026-08] [--allow-partial]

``--allow-partial`` lets an unfinished month run month-to-date (the summary
is banner-marked PARTIAL); without it an open month fails loudly.
"""

from __future__ import annotations

import logging
import os
import sys
from decimal import Decimal
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "scripts"))

from _months_arg import filter_by_months  # noqa: E402

from settle.compute.tmf import TmfPolicy, compute_tmf_monthly, write_tmf  # noqa: E402
from settle.domain import Month  # noqa: E402
from settle.extract.tmf_state import read_tmf_state  # noqa: E402
from settle.normalize.sources.hypersync_sbe import (  # noqa: E402
    HyperSyncSbeSource,
    month_block_range,
)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    missing = [v for v in ("ETH_RPC", "ENVIO_API_TOKEN") if not os.environ.get(v)]
    if missing:
        print("Missing required env vars:")
        for v in missing:
            print(f"  - {v}")
        print("\nHint: `set -a; source .env; set +a` from the repo root.")
        return 2

    cfg = yaml.safe_load((_REPO / "config" / "tmf.yaml").read_text())
    policy = TmfPolicy.from_config(cfg)
    contracts: dict[str, str] = cfg["contracts"]
    months = [Month.parse(k) for k in sorted(cfg["months"])]
    selected = filter_by_months(months, lambda m: (m.year, m.month))
    source = HyperSyncSbeSource(contracts)
    allow_partial = "--allow-partial" in sys.argv

    print("TMF 2026 — Treasury Management Function waterfall + Smart Burn Engine execution")
    print("=" * 108)
    print(f"{'Month':<9} {'SNR':>14} {'Step 3 budget':>15} {'hop':>7} {'vestTot SKY':>14} "
          f"{'SKY bought':>14} {'SKY to burn':>14} {'checks':>8}")
    print("-" * 108)
    written: dict[str, dict[str, Path]] = {}
    errors: dict[str, str] = {}
    for month in selected:
        label = str(month)
        mcfg = cfg["months"][label]
        try:
            from_block, to_block, from_ts, to_ts, partial = month_block_range(
                month, allow_partial=allow_partial,
            )
            state_start = read_tmf_state(contracts, from_block - 1)
            state_end = read_tmf_state(contracts, to_block)
            spell_blk = (mcfg.get("spell") or {}).get("executed_block")
            spell_state = read_tmf_state(contracts, int(spell_blk)) if spell_blk else None
            activity = source.activity(
                month, from_block, to_block, from_ts=from_ts, to_ts=to_ts,
                burn_at_start=Decimal(state_start["splitter_burn"]),
                hop_at_start=int(state_start["splitter_hop"]),
            )
            r = compute_tmf_monthly(
                label, mcfg, policy, activity=activity, state=state_end,
                spell_state=spell_state, repo_root=_REPO,
                pins={
                    "from_block": from_block, "to_block": to_block,
                    "from_ts": from_ts, "to_ts": to_ts,
                    "state_start_block": from_block - 1,
                    "spell_state_block": spell_blk,
                    "partial": partial,
                    "hypersync_endpoint": "eth.hypersync.xyz",
                },
            )
            written[label] = write_tmf(r, _REPO / "settlements" / "tmf" / label)
            ok = sum(1 for c in r.checks if c["ok"])
            print(
                f"{label:<9} {float(r.inputs.snr):>14,.0f} {float(r.waterfall.step3_capital):>15,.2f} "
                f"{r.waterfall.hop:>7,} {float(r.waterfall.vest_tot):>14,.0f} "
                f"{float(r.sky_bought_month):>14,.2f} {float(r.sky_to_burn):>14,.2f} "
                f"{ok}/{len(r.checks):>5}"
                + (f"   ⚠ {len(r.warnings)} warning(s)" if r.warnings else ""),
                flush=True,
            )
        except Exception as exc:  # one month failing must not hide the others
            logging.getLogger("tmf").exception("%s failed", label)
            errors[label] = f"{type(exc).__name__}: {exc}"
            print(f"{label:<9}  ✗ FAILED: {errors[label]}", flush=True)
    print("-" * 108)
    if written:
        print("\nArtifacts written:\n")
        for label, paths in written.items():
            print(f"  {label}:")
            for kind, p in paths.items():
                print(f"    {kind:<10} {p.relative_to(_REPO)}")
    if errors:
        print(f"\n{len(errors)} month(s) failed:")
        for label, msg in errors.items():
            print(f"  {label}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
