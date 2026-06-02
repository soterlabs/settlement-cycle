"""Grove May 2026 settlement runner.

Backed by the ``grove_2026_05`` fixture set (captured 2026-06-02 via Dune).
Pin blocks correspond to May 31 23:59:59 UTC EoD per chain.

Run with:
    ETH_RPC=… BASE_RPC=… AVALANCHE_C_RPC=… PLUME_RPC=… MONAD_RPC=… \\
    PYTHONPATH=src python3 scripts/run_grove_2026_05.py
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

# May 2026 pin blocks (captured via blocks_at_eod on 2026-06-02). SoM = April EoM.
PIN_BLOCKS_SOM = {
    Chain.ETHEREUM: 24996367, Chain.BASE: 45402126,
    Chain.AVALANCHE_C: 84298393, Chain.PLUME: 65382097,
    Chain.MONAD: 71616121,
}
PIN_BLOCKS_EOM = {
    Chain.ETHEREUM: 25218797, Chain.BASE: 46741326,
    Chain.AVALANCHE_C: 86865826, Chain.PLUME: 71786194,
    Chain.MONAD: 78309381,
}

_SETTLEMENT_SOURCES = {
    "debt": "DuneDebtSource (fixture: grove_2026_05/dune_outputs.json)",
    "balance": "DuneBalanceSource (fixture)",
    "ssr": "DuneSSRSource (fixture)",
    "position_balance": "RPCPositionBalanceSource (publicnode)",
    "convert_to_assets": "RPCConvertToAssetsSource (publicnode)",
    "nav_oracle (chronicle)": "ChronicleNavSource",
    "nav_oracle (const_one)": "ConstOneNavSource",
    "lp_curve": "CurvePoolSource",
    "lp_uniswap_v3": "RPCUniswapV3PositionSource + Dune-fixture events",
}


def main() -> int:
    grove, fixtures, blocks_by_chain = load_grove_and_fixtures(
        _REPO, fixture_dir="grove_2026_05",
    )
    sources = build_grove_sources(grove, fixtures, blocks_by_chain)

    print("Grove May 2026 settlement (estimate — no Grove reference yet)")
    print("=" * 100)
    result = compute_monthly_pnl(
        grove, Month(2026, 5),
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

    out = _REPO / "settlements" / "grove" / "2026-05"
    write_settlement(result, out, sources=_SETTLEMENT_SOURCES)
    print(f"\nWrote to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
