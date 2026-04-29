"""Grove Q1 2026 multi-month settlement (Jan / Feb / Mar 2026).

Reuses the same source-assembly pattern as ``run_grove_2026_03.py`` (Dune
fixtures from prime start through Mar EoM cover all three months; only
``pin_blocks`` change per month). Computes ``prime_agent_total_revenue``,
``sky_revenue`` (with Sky Direct Step 4 absorption applied to E9 JTRSY and
E10 BUIDL), and ``sky_direct_shortfall`` for each month.

Run with:
    ETH_RPC=… BASE_RPC=… AVALANCHE_C_RPC=… PLUME_RPC=… \\
    PYTHONPATH=src python3 scripts/run_grove_2026_q1.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

from settle.compute import compute_monthly_pnl
from settle.domain import Chain, Month
from settle.load import write_settlement
from tests.fixtures.grove_fixture_loader import (
    build_grove_sources,
    load_grove_and_fixtures,
)

_SETTLEMENT_SOURCES = {
    "debt": "DuneDebtSource (MCP fixture: dune_outputs.json)",
    "balance": "DuneBalanceSource (MCP fixture)",
    "ssr": "DuneSSRSource (MCP fixture)",
    "position_balance": "RPCPositionBalanceSource (Alchemy)",
    "convert_to_assets": "RPCConvertToAssetsSource (Alchemy)",
    "nav_oracle (chronicle)": "ChronicleNavSource (Alchemy) + nav_overrides for pre-deployment blocks",
    "nav_oracle (const_one)": "ConstOneNavSource (in-process)",
    "lp_curve": "CurvePoolSource (Alchemy)",
    "lp_uniswap_v3": "RPCUniswapV3PositionSource + Dune-fixture events",
}

# Pin blocks per (month, chain). EoM block = last block ≤ <last day of month> 23:59:59 UTC.
PIN_BLOCKS_BY_MONTH = {
    # SoM = previous month's EoM
    (2026, 1): {
        "som": {Chain.ETHEREUM: 24136052, Chain.BASE: 40218126,
                Chain.AVALANCHE_C: 74824633, Chain.PLUME: 44691271},
        "eom": {Chain.ETHEREUM: 24358292, Chain.BASE: 41557326,
                Chain.AVALANCHE_C: 76986991, Chain.PLUME: 49010253},
    },
    (2026, 2): {
        "som": {Chain.ETHEREUM: 24358292, Chain.BASE: 41557326,
                Chain.AVALANCHE_C: 76986991, Chain.PLUME: 49010253},
        "eom": {Chain.ETHEREUM: 24558867, Chain.BASE: 42766926,
                Chain.AVALANCHE_C: 79250451, Chain.PLUME: 52322002},
    },
    (2026, 3): {
        "som": {Chain.ETHEREUM: 24558867, Chain.BASE: 42766926,
                Chain.AVALANCHE_C: 79250451, Chain.PLUME: 52322002},
        "eom": {Chain.ETHEREUM: 24781026, Chain.BASE: 44106126,
                Chain.AVALANCHE_C: 81789468, Chain.PLUME: 58679343},
    },
}


def main() -> int:
    grove, fixtures, blocks_by_chain = load_grove_and_fixtures(_REPO)

    print("Grove Q1 2026 multi-month settlement (with Sky Direct Step 4 applied)")
    print("=" * 100)
    print(f"{'Month':<10} {'prime_agent_total':>20} {'sky_revenue':>20} {'sky_direct_shortfall':>22} {'monthly_pnl':>16}")
    print("-" * 100)

    results = {}
    written_paths: dict[tuple[int, int], dict] = {}
    for (y, m) in [(2026, 1), (2026, 2), (2026, 3)]:
        # Rebuild Sources per month so each MockBalanceSource has a fresh
        # call-recording slate (avoids leaking state across months).
        sources = build_grove_sources(grove, fixtures, blocks_by_chain)
        pin = PIN_BLOCKS_BY_MONTH[(y, m)]
        result = compute_monthly_pnl(
            grove, Month(y, m),
            sources=sources,
            pin_blocks_eom=pin["eom"],
            pin_blocks_som=pin["som"],
        )
        results[(y, m)] = result
        label = f"{y}-{m:02d}"
        print(f"{label:<10} ${float(result.prime_agent_total_revenue):>19,.2f} "
              f"${float(result.sky_revenue):>19,.2f} "
              f"${float(result.sky_direct_shortfall):>21,.2f} "
              f"${float(result.monthly_pnl):>15,.2f}")

        # Persist the headline + per-venue breakdown + provenance for each month.
        # Same artifact set as the single-month acceptance script writes — auditors
        # / downstream rollups consume these regardless of which entry point ran.
        out_dir = _REPO / "settlements" / "grove" / label
        written_paths[(y, m)] = write_settlement(
            result, out_dir, sources=_SETTLEMENT_SOURCES,
        )

    print("-" * 100)
    print()
    print("Per-venue revenue breakdown (top contributors per month):")
    print("=" * 110)
    for (y, m), result in results.items():
        label = f"{y}-{m:02d}"
        print(f"\n  {label}:  prime_agent_revenue=${float(result.prime_agent_revenue):,.2f}")
        # All venues with abs(revenue) > $1K
        sorted_venues = sorted(result.venue_breakdown, key=lambda vr: -abs(float(vr.revenue)))
        for vr in sorted_venues:
            if abs(float(vr.revenue)) < 1000 and abs(float(vr.actual_revenue)) < 1000:
                continue
            sd = " [SD]" if vr.br_charge > 0 else ""
            print(f"    {vr.venue_id:<5} {vr.label[:40]:<40}{sd:<5} "
                  f"value_som={float(vr.value_som):>14,.0f}  value_eom={float(vr.value_eom):>14,.0f}  "
                  f"inflow={float(vr.period_inflow):>14,.0f}  actual_rev={float(vr.actual_revenue):>13,.0f}  "
                  f"revenue={float(vr.revenue):>13,.0f}")

    print()
    print("Per-venue Sky Direct breakdown:")
    print("=" * 110)
    for (y, m), result in results.items():
        label = f"{y}-{m:02d}"
        sd_venues = [vr for vr in result.venue_breakdown if vr.br_charge > 0]
        if not sd_venues:
            continue
        print(f"\n  {label}:")
        print(f"    {'venue':<22} {'value_som':>14} {'value_eom':>14} {'inflow':>14} {'actual_rev':>12} {'BR_charge':>12} {'shortfall':>11} {'prime_keeps':>12}")
        for vr in sd_venues:
            print(f"    {vr.venue_id + ' ' + vr.label[:18]:<22} "
                  f"{float(vr.value_som):>14,.0f} {float(vr.value_eom):>14,.0f} {float(vr.period_inflow):>14,.0f} "
                  f"{float(vr.actual_revenue):>12,.0f} {float(vr.br_charge):>12,.0f} "
                  f"{float(vr.sky_direct_shortfall):>11,.0f} {float(vr.revenue):>12,.0f}")

    print()
    print("Artifacts written:")
    print("=" * 110)
    for (y, m), paths in written_paths.items():
        label = f"{y}-{m:02d}"
        print(f"\n  {label}:")
        for k, p in paths.items():
            print(f"    {k:11s} {p.relative_to(_REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
