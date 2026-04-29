"""Phase 2.A.6 — Grove March 2026 acceptance run.

Runs the full settlement pipeline for Grove against Ethereum-only data.
Dune-backed primitives are sourced from a fixture captured via MCP on
2026-04-27 (`tests/fixtures/grove_2026_03/dune_outputs.json`); RPC-backed
primitives (positions, oracles, Curve pool, V3 NFTs) hit the live RPC.

Run with:
    ETH_RPC=<alchemy url> PYTHONPATH=src python3 scripts/run_grove_2026_03.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

# Allow running from anywhere — add src/ to path.
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

PIN_BLOCKS_EOM = {
    Chain.ETHEREUM:    24781026,    # 2026-03-31 23:59:59 UTC
    Chain.BASE:        44106126,
    Chain.AVALANCHE_C: 81789468,
    Chain.PLUME:       58679343,
}
PIN_BLOCKS_SOM = {
    Chain.ETHEREUM:    24558867,    # 2026-02-28 23:59:59 UTC
    Chain.BASE:        42766926,
    Chain.AVALANCHE_C: 79250451,
    Chain.PLUME:       52322002,
}


def main() -> int:
    grove, fixtures, blocks_by_chain = load_grove_and_fixtures(_REPO)
    sources = build_grove_sources(grove, fixtures, blocks_by_chain)

    print(f"Running Grove 2026-03 settlement (Phase 2.A.6 acceptance)...")
    print(f"  EoM block: {PIN_BLOCKS_EOM[Chain.ETHEREUM]}")
    print(f"  SoM block: {PIN_BLOCKS_SOM[Chain.ETHEREUM]}")

    result = compute_monthly_pnl(
        grove, Month(2026, 3),
        sources=sources,
        pin_blocks_eom=PIN_BLOCKS_EOM,
        pin_blocks_som=PIN_BLOCKS_SOM,
    )

    print()
    print("=" * 80)
    print(f"  GROVE — Monthly settlement 2026-03")
    print("=" * 80)
    print(f"  prime_agent_revenue:        ${result.prime_agent_revenue:>20,.2f}")
    print(f"  agent_rate:                 ${result.agent_rate:>20,.2f}")
    print(f"  distribution_rewards:       ${result.distribution_rewards:>20,.2f}")
    print(f"  ─────────────────────────────────────────────────")
    print(f"  prime_agent_total_revenue:  ${result.prime_agent_total_revenue:>20,.2f}")
    print(f"  sky_revenue (net):          ${result.sky_revenue:>20,.2f}")
    if result.sky_direct_shortfall != 0:
        print(f"    ↳ sky_direct_shortfall:   ${result.sky_direct_shortfall:>20,.2f}")
    print()
    print(f"  Per-venue revenue:")
    for v in result.venue_breakdown:
        marker = "✓" if v.revenue >= 0 else "↓"
        print(f"    {v.venue_id:5s} {marker} {v.label[:40]:40s}  Δvalue=${v.value_eom - v.value_som:>15,.2f}  inflow=${v.period_inflow:>13,.2f}")

    # Cost-basis invariant sanity (rough): Σ value > Σ debt × utilization_factor
    sum_value_eom = sum((v.value_eom for v in result.venue_breakdown), Decimal("0"))
    cum_debt_eom = Decimal("2809067986.72")
    print(f"\n  Σ value_eom across venues: ${sum_value_eom:,.2f}")
    print(f"  cum_debt at EoM (from fixture): ${cum_debt_eom:,.2f}")
    diff = sum_value_eom - cum_debt_eom
    pct = float(diff / cum_debt_eom) * 100
    print(f"  Δ (value − debt):          ${diff:,.2f}  ({pct:+.2f}%)")
    print(f"     (positive = accumulated yield since prime start, plus any")
    print(f"      in-period new principal not yet reflected in cum_debt)")

    output = _REPO / "settlements" / "grove" / "2026-03"
    written = write_settlement(result, output, sources={
        "debt": "DuneDebtSource (MCP fixture: dune_outputs.json)",
        "balance": "DuneBalanceSource (MCP fixture)",
        "ssr": "DuneSSRSource (MCP fixture)",
        "position_balance": "RPCPositionBalanceSource (Alchemy)",
        "convert_to_assets": "RPCConvertToAssetsSource (Alchemy)",
        "nav_oracle (chronicle)": "ChronicleNavSource (Alchemy)",
        "nav_oracle (const_one)": "ConstOneNavSource (in-process)",
        "lp_curve": "CurvePoolSource (Alchemy)",
        "lp_uniswap_v3": "RPCUniswapV3PositionSource (Alchemy)",
    })
    print()
    print(f"  Artifacts written:")
    for k, p in written.items():
        print(f"    {k:11s} {p.relative_to(_REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
