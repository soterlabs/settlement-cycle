"""OBEX 2026 multi-month settlement runner — Jan through May.

Single entry point for the OBEX prime. OBEX is single-chain (Ethereum-only)
with one venue (V1 = Maple syrupUSDC), so the run loop is much simpler
than Spark/Grove and doesn't need pre-captured fixtures — live Dune + RPC
sources cover everything needed in well under a minute per month from a
warm cache (~28 RPC ``ilk_rate`` calls/month + 1 Dune ``debt_timeseries``
query + per-day balance reads).

For each month, the loop:
  1. Builds live ``Sources`` (Dune for debt + balances + SSR, RPC for
     position balances + ERC-4626 convertToAssets). The orchestrator
     upgrades the block resolver to ``DuneBlockResolver`` when
     ``DUNE_API_KEY`` is set, replacing ~25 per-day binary-search RPC
     calls with one Dune query.
  2. Runs ``compute_monthly_pnl(obex, month, sources=...)`` — the
     orchestrator resolves SoM/EoM pin blocks via the block resolver.
  3. Persists ``provenance.json`` + ``summary.md`` + the canonical xlsx
     under ``settlements/obex/<YYYY-MM>/`` via ``write_settlement``.

Required env vars (sourced from ``.env`` via ``set -a; source .env;
set +a``):
    DUNE_API_KEY        — Dune key for the debt / balance / SSR queries
    ETH_RPC             — Ethereum RPC endpoint (archival, for past blocks)

Optional:
    DATABASE_URL        — Postgres raw-data cache (read-through; speeds
                          up re-runs but not required)

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python3 scripts/run_obex_2026.py
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

from settle.compute import Sources, compute_monthly_pnl  # noqa: E402
from settle.domain import Month  # noqa: E402
from settle.domain.config import load_prime  # noqa: E402
from settle.load import write_settlement  # noqa: E402
from settle.normalize.registry import (  # noqa: E402
    get_balance_source,
    get_convert_to_assets_source,
    get_debt_source,
    get_position_balance_source,
    get_ssr_source,
)

_OBEX_YAML = _REPO / "config" / "obex.yaml"
_MONTHS = [Month(2026, m) for m in (1, 2, 3, 4, 5, 6)]

# Documented in provenance.json so an auditor can see at a glance which
# upstream sources fed each settlement run.
_SOURCES_LIVE = {
    "debt":              "DuneDebtSource",
    "balance":           "DuneBalanceSource",
    "ssr":               "DuneSSRSource",
    "position_balance":  "RPCPositionBalanceSource",
    "convert_to_assets": "RPCConvertToAssetsSource",
    "block_resolver":    "DuneBlockResolver (orchestrator-upgraded) + RPC fallback",
}


def _check_env() -> None:
    """OBEX needs only DUNE_API_KEY + ETH_RPC (single-chain prime)."""
    missing = [v for v in ("DUNE_API_KEY", "ETH_RPC") if not os.environ.get(v)]
    if missing:
        print("Missing required env vars:")
        for v in missing:
            print(f"  - {v}")
        print("\nHint: `set -a; source .env; set +a` from the repo root.")
        raise SystemExit(1)


def _live_sources() -> Sources:
    """Live sources with ``block_resolver`` left ``None`` so the
    orchestrator upgrades it to ``DuneBlockResolver`` per chain (one
    Dune query per chain replaces ~25 binary-search RPC calls/day)."""
    return Sources(
        debt=get_debt_source(),
        balance=get_balance_source(),
        ssr=get_ssr_source(),
        position_balance=get_position_balance_source(),
        convert_to_assets=get_convert_to_assets_source(),
    )


def main() -> int:
    if "--dr-only" in sys.argv:
        # Refresh Distribution Rewards from settle-dr-dune into the existing
        # reports — no recompute (no RPC / Dune). obex has no tagged DR, so
        # this is a fast no-op; the flag still prevents a full recompute.
        from settle.load import refresh_dr_only
        print("OBEX — DR-only refresh from settle-dr-dune (no recompute)")
        refresh_dr_only("obex")
        return 0

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )
    _check_env()

    prime = load_prime(_OBEX_YAML)

    print(f"OBEX 2026 multi-month settlement (Jan → Jun)")
    print("=" * 110)
    print(f"{'Month':<10} {'prime_agent_total':>21} {'sky_revenue':>17} "
          f"{'sky_direct_shortfall':>22} {'monthly_pnl':>17}")
    print("-" * 110)

    errors: list[tuple[str, str]] = []
    artifacts: list[tuple[str, dict[str, Path]]] = []

    for month in _MONTHS:
        label = f"{month.year}-{month.month:02d}"
        try:
            sources = _live_sources()
            result = compute_monthly_pnl(prime, month, sources=sources)
            out_dir = _REPO / "settlements" / "obex" / label
            paths = write_settlement(result, out_dir, sources=_SOURCES_LIVE)
            artifacts.append((label, paths))
            print(
                f"{label:<10} "
                f"${float(result.prime_agent_total_revenue):>20,.2f} "
                f"${float(result.sky_revenue):>16,.2f} "
                f"${float(result.sky_direct_shortfall):>21,.2f} "
                f"${float(result.monthly_pnl):>16,.2f}",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001 — keep going across months
            msg = f"{type(e).__name__}: {e}"
            errors.append((label, msg))
            print(f"{label:<10}  ✗ FAILED: {msg}", flush=True)
            traceback.print_exc()

    print("-" * 110)
    if artifacts:
        print("\nArtifacts written:\n")
        for label, paths in artifacts:
            print(f"  {label}:")
            for kind in ("provenance", "summary", "xlsx"):
                p = paths.get(kind)
                if p is not None:
                    print(f"    {kind:<10} {p.relative_to(_REPO)}")
    if errors:
        print(f"\n{len(errors)} month(s) failed:")
        for label, msg in errors:
            print(f"  {label}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
