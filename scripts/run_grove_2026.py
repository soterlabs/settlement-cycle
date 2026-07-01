"""Grove 2026 multi-month settlement runner — Jan through June.

Single entry point that exercises every existing Grove fixture:

  * Jan / Feb / Mar → ``replay/grove_2026_03/`` (each month uses
    its own SoM / EoM pin blocks; the Dune/RPC inputs cover Q1 entirely).
  * Apr            → ``replay/grove_2026_04/``.
  * May            → ``replay/grove_2026_05/``.
  * Jun            → ``replay/grove_2026_06/``.

For each month, the loop:
  1. (Re)loads the right fixture set.
  2. Rebuilds Sources so each ``MockBalanceSource`` gets a fresh
     call-recording slate (avoids leaking state across months).
  3. Runs ``compute_monthly_pnl`` with the month's SoM / EoM pin blocks.
  4. Persists ``provenance.json`` + ``summary.md`` + the canonical xlsx
     under ``settlements/grove/<YYYY-MM>/`` via ``write_settlement``.

Run with:
    PYTHONPATH=src python3 scripts/run_grove_2026.py
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

from settle.compute import compute_monthly_pnl
from settle.domain import Month
from settle.domain.pin_blocks import month_pins
from settle.load import write_settlement
from settle.normalize.registry import get_ssr_source
from replay.grove_fixture_loader import (
    build_grove_sources,
    load_grove_and_fixtures,
)

# Pin blocks live in ``config/pin_blocks.yaml`` (single source of truth,
# shared with the Spark runner + capture scripts). Read via
# ``settle.domain.pin_blocks.month_pins`` below.

# (year, month, fixture_dir). Q1 months all share grove_2026_03.
_MONTH_PLAN = [
    (2026, 1, "grove_2026_03"),
    (2026, 2, "grove_2026_03"),
    (2026, 3, "grove_2026_03"),
    (2026, 4, "grove_2026_04"),
    (2026, 5, "grove_2026_05"),
    (2026, 6, "grove_2026_06"),
]


def _sources_manifest(fixture_dir: str) -> dict[str, str]:
    return {
        "debt":                    f"DuneDebtSource (MCP fixture: {fixture_dir}/dune_outputs.json)",
        "balance":                 "DuneBalanceSource (MCP fixture)",
        "ssr":                     "DuneSSRSource (live on-chain read at the period pin block)",
        "position_balance":        "RPCPositionBalanceSource",
        "convert_to_assets":       "RPCConvertToAssetsSource",
        "nav_oracle (chronicle)":  "ChronicleNavSource",
        "nav_oracle (const_one)":  "ConstOneNavSource (in-process)",
        "lp_curve":                "CurvePoolSource",
        "lp_uniswap_v3":           "RPCUniswapV3PositionSource + Dune-fixture events",
    }


def main() -> int:
    if "--dr-only" in sys.argv:
        # Refresh Distribution Rewards from settle-dr-dune into the existing
        # reports — no recompute (no RPC / Dune). Needs a prior full run.
        from settle.load import refresh_dr_only
        print("Grove — DR-only refresh from settle-dr-dune (no recompute)")
        refresh_dr_only("grove")
        return 0

    print("Grove 2026 multi-month settlement (Jan → June)")
    print("=" * 110)
    print(f"{'Month':<10} {'prime_agent_total':>20} {'sky_revenue':>16} "
          f"{'sky_direct_shortfall':>22} {'monthly_pnl':>16}")
    print("-" * 110)

    written_paths: dict[tuple[int, int], dict] = {}
    cached_fixture: str | None = None
    grove = fixtures = blocks_by_chain = None

    for (y, m, fixture_dir) in _MONTH_PLAN:
        # Only reload fixtures when the fixture dir changes — Q1 shares one.
        if fixture_dir != cached_fixture:
            grove, fixtures, blocks_by_chain = load_grove_and_fixtures(
                _REPO, fixture_dir=fixture_dir,
            )
            cached_fixture = fixture_dir

        # Rebuild Sources per month so each MockBalanceSource gets a fresh
        # call-recording slate (avoids leaking state across months).
        sources = build_grove_sources(grove, fixtures, blocks_by_chain)
        # Stop freezing SSR: read it live at the period pin block rather than
        # the per-month fixture snapshot (deterministic historical read; keeps
        # SSR always current — see run_spark_2026.py for the rationale).
        sources = dataclasses.replace(sources, ssr=get_ssr_source())
        pin = month_pins(y, m, chains=grove.chains)

        result = compute_monthly_pnl(
            grove, Month(y, m),
            sources=sources,
            pin_blocks_eom=pin["eom"],
            pin_blocks_som=pin["som"],
        )
        # Fold in DR so the console headline matches the written report
        # (write_settlement enriches a copy; this enriches the printed one).
        from settle.load import enrich_with_dr
        result = enrich_with_dr(result)

        label = f"{y}-{m:02d}"
        print(f"{label:<10} ${float(result.prime_agent_total_revenue):>19,.2f} "
              f"${float(result.sky_revenue):>15,.2f} "
              f"${float(result.sky_direct_shortfall):>21,.2f} "
              f"${float(result.monthly_pnl):>15,.2f}")

        out_dir = _REPO / "settlements" / "grove" / label
        written_paths[(y, m)] = write_settlement(
            result, out_dir, sources=_sources_manifest(fixture_dir),
        )

    print("-" * 110)
    print()
    print("Artifacts written:")
    for (y, m), paths in written_paths.items():
        label = f"{y}-{m:02d}"
        print(f"\n  {label}:")
        for k, p in paths.items():
            print(f"    {k:11s} {p.relative_to(_REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
