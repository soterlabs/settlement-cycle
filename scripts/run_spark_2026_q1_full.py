"""Spark Q1 2026 — full ``compute_monthly_pnl`` (prime_agent_revenue + sky_revenue + agent_rate).

Wires the Spark fixture loader (`tests/fixtures/spark_fixture_loader.py`)
through `compute.compute_monthly_pnl`. Mirrors the structure of
`scripts/run_grove_2026_q1.py`.

Scope of the unblocked slice (per PRD §17.12):
* Cat A (15 venues): revenue = $0 with empty `external_alm_sources`; SoM/EoM
  value snapshots come from RPC `balanceOf`.
* Cat B (17 venues): cum_balance from `cat_b_cum_balance.json`; pricing via
  RPC `convertToAssets`.
* Cat C (12 venues): pure RPC `balanceOf` + `scaledBalanceOf`.
* Cat E (5 venues): cum_balance from `cat_e_cum_balance.json`. **All Cat E
  positions are $0 by Q1 2026** (Spark exited every RWA tranche by Dec 2025);
  contribution to revenue + Sky Direct shortfall is ~$0.
* Cat F (S24, S25): pure RPC Curve pool reads.

PSM3 (utilized reduction) is read live via RPC; results are cached from the
prior `run_spark_2026_q1.py` run.

Run with:
    PYTHONPATH=src python3 scripts/run_spark_2026_q1_full.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

from settle.compute import compute_monthly_pnl  # noqa: E402
from settle.domain import Chain, Month  # noqa: E402
from settle.load import write_settlement  # noqa: E402
from tests.fixtures.spark_fixture_loader import (  # noqa: E402
    build_spark_sources,
    load_spark_and_fixtures,
)

_SETTLEMENT_SOURCES = {
    "debt": "MockDebtSource backed by tests/fixtures/spark_2026_q1/debt_timeseries.json",
    "balance": "Routed MockBalanceSource (Cat B + Cat E from spark_2026_q1 fixtures; Cat A stubbed; PSM directed-flow stubbed)",
    "ssr": "MockSSRSource (reused from grove_2026_03 — Sky-wide)",
    "position_balance": "RPCPositionBalanceSource (Alchemy/drpc)",
    "convert_to_assets": "RPCConvertToAssetsSource (Alchemy/drpc)",
    "psm3": "RPCPsm3Source (drpc — cached from sky_revenue run)",
    "block_resolver": "FixtureMultiResolver (date->block from spark_2026_q1 fixtures, RPC fallback)",
    "curve_pool": "CurvePoolSource (Alchemy)",
}

# Pin blocks per (month, chain). Eth + Base from Grove's fixtures (verified);
# Arb/Op/Uni from Dune query 7401735; Avalanche-C from Dune query 7402172.
PIN_BLOCKS_BY_MONTH = {
    (2026, 1): {
        "som": {Chain.ETHEREUM: 24136052, Chain.BASE: 40218126,
                Chain.ARBITRUM: 416593973, Chain.OPTIMISM: 145813411,
                Chain.UNICHAIN: 36477240, Chain.AVALANCHE_C: 74824633},
        "eom": {Chain.ETHEREUM: 24358292, Chain.BASE: 41557326,
                Chain.ARBITRUM: 427315178, Chain.OPTIMISM: 147152611,
                Chain.UNICHAIN: 39155640, Chain.AVALANCHE_C: 76986991},
    },
    (2026, 2): {
        "som": {Chain.ETHEREUM: 24358292, Chain.BASE: 41557326,
                Chain.ARBITRUM: 427315178, Chain.OPTIMISM: 147152611,
                Chain.UNICHAIN: 39155640, Chain.AVALANCHE_C: 76986991},
        "eom": {Chain.ETHEREUM: 24558867, Chain.BASE: 42766926,
                Chain.ARBITRUM: 437025050, Chain.OPTIMISM: 148362211,
                Chain.UNICHAIN: 41574840, Chain.AVALANCHE_C: 79250451},
    },
    (2026, 3): {
        "som": {Chain.ETHEREUM: 24558867, Chain.BASE: 42766926,
                Chain.ARBITRUM: 437025050, Chain.OPTIMISM: 148362211,
                Chain.UNICHAIN: 41574840, Chain.AVALANCHE_C: 79250451},
        "eom": {Chain.ETHEREUM: 24781026, Chain.BASE: 44106126,
                Chain.ARBITRUM: 447736930, Chain.OPTIMISM: 149701411,
                Chain.UNICHAIN: 44253240, Chain.AVALANCHE_C: 81789468},
    },
}


def main() -> int:
    spark, fixtures = load_spark_and_fixtures(_REPO)

    print("Spark Q1 2026 — full prime_agent_total_revenue + sky_revenue + agent_rate")
    print("=" * 110)
    print(f"  Chains:  {sorted(c.value for c in spark.alm.keys())}")
    print(f"  Venues:  {len(spark.venues)} (A: {sum(1 for v in spark.venues if v.pricing_category.value=='A')}, "
          f"B: {sum(1 for v in spark.venues if v.pricing_category.value=='B')}, "
          f"C: {sum(1 for v in spark.venues if v.pricing_category.value=='C')}, "
          f"E: {sum(1 for v in spark.venues if v.pricing_category.value=='E')}, "
          f"F: {sum(1 for v in spark.venues if v.pricing_category.value=='F')})")
    print()
    print(f"{'Month':<10} {'prime_agent_total':>20} {'sky_revenue':>16} {'sky_direct_shortfall':>22} {'monthly_pnl':>16}")
    print("-" * 110)

    results = {}
    written_paths: dict[tuple[int, int], dict] = {}

    for ym in [(2026, 1), (2026, 2), (2026, 3)]:
        # Rebuild sources per month so each MockBalanceSource gets a fresh
        # call-recording slate (avoids leaking state across months). Cat A
        # cum_balance is synthesized via RPC at the per-month SoM/EoM blocks.
        pins = PIN_BLOCKS_BY_MONTH[ym]
        sources = build_spark_sources(
            spark, fixtures,
            pin_blocks_som=pins["som"], pin_blocks_eom=pins["eom"],
        )

        result = compute_monthly_pnl(
            spark, Month(*ym),
            sources=sources,
            pin_blocks_eom=pins["eom"],
            pin_blocks_som=pins["som"],
        )
        results[ym] = result
        label = f"{ym[0]}-{ym[1]:02d}"
        print(f"{label:<10} ${float(result.prime_agent_total_revenue):>19,.2f} "
              f"${float(result.sky_revenue):>15,.2f} "
              f"${float(result.sky_direct_shortfall):>21,.2f} "
              f"${float(result.monthly_pnl):>15,.2f}")

        out_dir = _REPO / "settlements" / "spark" / label
        written_paths[ym] = write_settlement(
            result, out_dir, sources=_SETTLEMENT_SOURCES,
        )

    print("-" * 110)
    print()
    print("Per-venue revenue breakdown (top contributors per month):")
    print("=" * 110)
    for ym, result in results.items():
        label = f"{ym[0]}-{ym[1]:02d}"
        print(f"\n  {label}: prime_agent_revenue=${float(result.prime_agent_revenue):,.2f}")
        sorted_venues = sorted(result.venue_breakdown, key=lambda vr: -abs(float(vr.revenue)))
        for vr in sorted_venues:
            if abs(float(vr.revenue)) < 1000 and abs(float(vr.actual_revenue)) < 1000:
                continue
            sd = " [SD]" if vr.br_charge > 0 else ""
            print(f"    {vr.venue_id:<5} {vr.label[:42]:<42}{sd:<5} "
                  f"value_som={float(vr.value_som):>14,.0f}  value_eom={float(vr.value_eom):>14,.0f}  "
                  f"inflow={float(vr.period_inflow):>14,.0f}  actual_rev={float(vr.actual_revenue):>13,.0f}  "
                  f"revenue={float(vr.revenue):>13,.0f}")

    print()
    print("Artifacts written:")
    for ym, paths in written_paths.items():
        label = f"{ym[0]}-{ym[1]:02d}"
        for k, p in paths.items():
            print(f"  {label}  {k:11s} {p.relative_to(_REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
