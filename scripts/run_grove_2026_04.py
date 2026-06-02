"""Grove April 2026 settlement runner.

Same pattern as ``run_grove_2026_q1.py`` but pinned to April month-end and
backed by the ``grove_2026_04`` fixture set (captured via Dune; see
``tests/fixtures/grove_2026_04/_capture_dune_fixtures.py``).

Run with:
    ETH_RPC=… BASE_RPC=… AVALANCHE_C_RPC=… PLUME_RPC=… MONAD_RPC=… \\
    PYTHONPATH=src python3 scripts/run_grove_2026_04.py
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

# Pin blocks copied from settlements/grove/2026-04/provenance.json (the prior
# fully-live run). SoM = Mar EoM. Monad placeholder block_at_or_before lookups
# go through the fixture-backed resolver now that the April fixture captured
# blocks_at_eod_monad.json.
PIN_BLOCKS_SOM = {
    Chain.ETHEREUM: 24781026, Chain.BASE: 44106126,
    Chain.AVALANCHE_C: 81789468, Chain.PLUME: 58679343,
    Chain.MONAD: 65143725,
}
PIN_BLOCKS_EOM = {
    Chain.ETHEREUM: 24996367, Chain.BASE: 45402126,
    Chain.AVALANCHE_C: 84298393, Chain.PLUME: 65382097,
    Chain.MONAD: 71616121,
}

_SETTLEMENT_SOURCES = {
    "debt": "DuneDebtSource (MCP fixture: grove_2026_04/dune_outputs.json)",
    "balance": "DuneBalanceSource (MCP fixture)",
    "ssr": "DuneSSRSource (MCP fixture)",
    "position_balance": "RPCPositionBalanceSource (Alchemy / publicnode)",
    "convert_to_assets": "RPCConvertToAssetsSource (Alchemy / publicnode)",
    "nav_oracle (chronicle)": "ChronicleNavSource",
    "nav_oracle (const_one)": "ConstOneNavSource (in-process)",
    "lp_curve": "CurvePoolSource",
    "lp_uniswap_v3": "RPCUniswapV3PositionSource + Dune-fixture events",
}


def main() -> int:
    grove, fixtures, blocks_by_chain = load_grove_and_fixtures(
        _REPO, fixture_dir="grove_2026_04",
    )
    sources = build_grove_sources(grove, fixtures, blocks_by_chain)

    print("Grove April 2026 settlement")
    print("=" * 100)
    result = compute_monthly_pnl(
        grove, Month(2026, 4),
        sources=sources,
        pin_blocks_eom=PIN_BLOCKS_EOM,
        pin_blocks_som=PIN_BLOCKS_SOM,
    )

    print(f"\n  prime_agent_total_revenue:  ${float(result.prime_agent_total_revenue):>18,.2f}")
    print(f"  sky_revenue:                ${float(result.sky_revenue):>18,.2f}")
    print(f"  monthly_pnl:                ${float(result.monthly_pnl):>18,.2f}")
    print(f"  sde_revenue (to Sky):       ${float(result.sde_revenue):>18,.2f}")

    print("\nPer-venue revenue (>$1K abs):")
    for vr in sorted(result.venue_breakdown, key=lambda v: -abs(float(v.revenue))):
        if abs(float(vr.revenue)) < 1000 and abs(float(vr.actual_revenue)) < 1000:
            continue
        sd = " [SD]" if vr.sd_share > 0 else ""
        print(
            f"  {vr.venue_id:<5} {vr.label[:40]:<40}{sd:<5} "
            f"som=${float(vr.value_som):>14,.0f} eom=${float(vr.value_eom):>14,.0f} "
            f"inflow=${float(vr.period_inflow):>14,.0f} "
            f"actual=${float(vr.actual_revenue):>13,.0f} "
            f"revenue=${float(vr.revenue):>13,.0f}"
        )

    out = _REPO / "settlements" / "grove" / "2026-04"
    write_settlement(result, out, sources=_SETTLEMENT_SOURCES)
    print(f"\nWrote to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
