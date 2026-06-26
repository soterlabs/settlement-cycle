"""Grove 2026 multi-month settlement runner — Jan through May.

Single entry point that exercises every existing Grove fixture:

  * Jan / Feb / Mar → ``tests/fixtures/grove_2026_03/`` (each month uses
    its own SoM / EoM pin blocks; the Dune/RPC inputs cover Q1 entirely).
  * Apr            → ``tests/fixtures/grove_2026_04/``.
  * May            → ``tests/fixtures/grove_2026_05/``.

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
from settle.domain import Chain, Month
from settle.load import write_settlement
from settle.normalize.registry import get_ssr_source
from tests.fixtures.grove_fixture_loader import (
    build_grove_sources,
    load_grove_and_fixtures,
)

# ── Pin blocks per (year, month). EoM block = last block ≤ <last day of
#    month> 23:59:59 UTC. SoM = previous month's EoM. Monad blocks for Q1
#    are monotonic placeholders (Monad mainnet wasn't active for Grove in
#    Q1 2026; E33 V3 reads degrade to $0 via the wrap in
#    ``normalize.positions._uniswap_v3_value``).
# ───────────────────────────────────────────────────────────────────────
PIN_BLOCKS_BY_MONTH = {
    (2026, 1): {
        "som": {Chain.ETHEREUM: 24136052, Chain.BASE: 40218126,
                Chain.AVALANCHE_C: 74824633, Chain.PLUME: 44691271,
                Chain.MONAD: 1},
        "eom": {Chain.ETHEREUM: 24358292, Chain.BASE: 41557326,
                Chain.AVALANCHE_C: 76986991, Chain.PLUME: 49010253,
                Chain.MONAD: 2},
    },
    (2026, 2): {
        "som": {Chain.ETHEREUM: 24358292, Chain.BASE: 41557326,
                Chain.AVALANCHE_C: 76986991, Chain.PLUME: 49010253,
                Chain.MONAD: 2},
        "eom": {Chain.ETHEREUM: 24558867, Chain.BASE: 42766926,
                Chain.AVALANCHE_C: 79250451, Chain.PLUME: 52322002,
                Chain.MONAD: 3},
    },
    (2026, 3): {
        "som": {Chain.ETHEREUM: 24558867, Chain.BASE: 42766926,
                Chain.AVALANCHE_C: 79250451, Chain.PLUME: 52322002,
                Chain.MONAD: 3},
        "eom": {Chain.ETHEREUM: 24781026, Chain.BASE: 44106126,
                Chain.AVALANCHE_C: 81789468, Chain.PLUME: 58679343,
                Chain.MONAD: 4},
    },
    (2026, 4): {
        "som": {Chain.ETHEREUM: 24781026, Chain.BASE: 44106126,
                Chain.AVALANCHE_C: 81789468, Chain.PLUME: 58679343,
                Chain.MONAD: 65143725},
        "eom": {Chain.ETHEREUM: 24996367, Chain.BASE: 45402126,
                Chain.AVALANCHE_C: 84298393, Chain.PLUME: 65382097,
                Chain.MONAD: 71616121},
    },
    (2026, 5): {
        "som": {Chain.ETHEREUM: 24996367, Chain.BASE: 45402126,
                Chain.AVALANCHE_C: 84298393, Chain.PLUME: 65382097,
                Chain.MONAD: 71616121},
        "eom": {Chain.ETHEREUM: 25218797, Chain.BASE: 46741326,
                Chain.AVALANCHE_C: 86865826, Chain.PLUME: 71786194,
                Chain.MONAD: 78309381},
    },
}

# (year, month, fixture_dir). Q1 months all share grove_2026_03.
_MONTH_PLAN = [
    (2026, 1, "grove_2026_03"),
    (2026, 2, "grove_2026_03"),
    (2026, 3, "grove_2026_03"),
    (2026, 4, "grove_2026_04"),
    (2026, 5, "grove_2026_05"),
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

    print("Grove 2026 multi-month settlement (Jan → May)")
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
        pin = PIN_BLOCKS_BY_MONTH[(y, m)]

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
