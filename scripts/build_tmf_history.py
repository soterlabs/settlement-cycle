#!/usr/bin/env python
"""Build the Smart Burn Engine history dataset for the dashboard.

Every Splitter kick since deployment (USDS to buy SKY, USDS to SKY stakers,
their sum, SKY bought) and every SKY burn, aggregated monthly / quarterly /
annually, written to ``settlements/tmf/data/`` as a versioned JSON plus two
per-event CSVs. The publish workflow mirrors the folder into
settlement-reports; msc-dashboard reads ``sbe_history.json``.

Extraction goes through the reorg-safe HyperSync log store, so with
``DATABASE_URL`` set a re-run fetches only the blocks since the last one.

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python3 scripts/build_tmf_history.py [--to-block N]

``--to-block`` pins the upper bound (default: the HyperSync archive head minus
the reorg margin, i.e. the latest FINALIZED block, so the run is persistable).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from settle.compute.tmf_history import (  # noqa: E402
    HistoryDataset,
    aggregate,
    write_history_dataset,
)
from settle.extract import hypersync, hypersync_store  # noqa: E402
from settle.normalize.sources.hypersync_sbe import HyperSyncSbeSource  # noqa: E402


def _arg(flag: str) -> str | None:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 >= len(sys.argv):
            raise SystemExit(f"{flag} requires a value")
        return sys.argv[i + 1]
    return None


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if not os.environ.get("ENVIO_API_TOKEN"):
        print("Missing ENVIO_API_TOKEN.\n\nHint: `set -a; source .env; set +a` from the repo root.")
        return 2

    cfg = yaml.safe_load((_REPO / "config" / "tmf.yaml").read_text())
    hist = cfg["history"]
    contracts: dict[str, str] = dict(cfg["contracts"])
    from_block = int(hist["from_block"])

    to_arg = _arg("--to-block")
    if to_arg is not None:
        to_block = int(to_arg)
    else:
        head = hypersync.archive_height("ethereum")
        to_block = head - hypersync_store._reorg_margin()
    to_ts = hypersync.block_timestamp("ethereum", to_block)

    source = HyperSyncSbeSource(contracts, flappers=hist["flappers"])
    print(f"SBE history — blocks {from_block:,} → {to_block:,} (finalized head)")
    activity = source.history(from_block, to_block)
    burns = source.sky_burns(
        from_block, to_block,
        sinks=hist["burn_sinks"], protocol_senders=hist["protocol_senders"],
    )
    ds = HistoryDataset(
        from_block=from_block, to_block=to_block, to_ts=to_ts,
        kicks=activity.kicks, burns=burns, param_changes=activity.param_changes,
        contracts={k: contracts[k] for k in ("MCD_SPLIT", "MCD_FLAP", "MCD_KICK", "SKY",
                                              "MCD_PAUSE_PROXY", "REWARDS_LSSKY_USDS")},
        notes=[
            "The Splitter was deployed 2024-09-17 (block 20,770,191); the first kick ran "
            "2024-11-14. Before Aug 2026 the farm leg pointed at the LSMKR USDS farm "
            "(Oct 2024 - May 2025) and then the LSSKY USDS farm; 'usds_to_stakers' covers both "
            "(the per-kick CSV carries the farm address).",
            "Kicks under splitter.burn = 100% have usds_to_stakers = 0 by construction.",
            "SKY burns: the 2025-06-30 executive burned 426,292,860.23 SKY held by the Pause "
            "Proxy (SKY.burn(), zero address). TMF-rule burns (10/55 of the previous month's "
            "buys) start with the September 2026 executive.",
            "Aggregates are quantized to 2 dp (price to 6 dp) for charting; the CSVs are exact.",
        ],
    )
    paths = write_history_dataset(ds, _REPO / "settlements" / "tmf" / "data")

    print(f"{'month':<8} {'kicks':>6} {'USDS buyback':>14} {'USDS→stakers':>14} {'USDS total':>14} "
          f"{'SKY bought':>16} {'SKY burn':>14}")
    print("-" * 92)
    for r in aggregate(ds.kicks, ds.burns, "monthly"):
        print(f"{r.period:<8} {r.kicks:>6} {float(r.usds_buyback):>14,.0f} "
              f"{float(r.usds_to_stakers):>14,.0f} {float(r.usds_total):>14,.0f} "
              f"{float(r.sky_bought):>16,.2f} {float(r.sky_burn_protocol):>14,.2f}")
    t = ds.totals
    print("-" * 92)
    print(f"{'total':<8} {t.kicks:>6} {float(t.usds_buyback):>14,.0f} "
          f"{float(t.usds_to_stakers):>14,.0f} {float(t.usds_total):>14,.0f} "
          f"{float(t.sky_bought):>16,.2f} {float(t.sky_burn_protocol):>14,.2f}")
    print("\nArtifacts written:\n")
    for kind, p in paths.items():
        print(f"  {kind:<6} {p.relative_to(_REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
