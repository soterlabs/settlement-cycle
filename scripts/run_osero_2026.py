"""OSERO 2026 settlement runner — July 2026 onward.

Single entry point for the Osero prime (Diamond PAU, ilk ALLOCATOR-PRYSM-A —
see config/osero.yaml for the architecture notes). Osero is single-chain
(Ethereum-only)
with two venues (O1 SparkLend spUSDS, O2 raw USDS at the PAU ALM), so the run
loop is much simpler than Spark/Grove and doesn't need pre-captured fixtures.
Sources are LIVE and mostly HyperSync: config/osero.yaml routes debt /
balance / position_balance to HyperSync (requires ENVIO_API_TOKEN); SSR is
the Dune on-chain query; RPC covers ``ilk_rate`` + convertToAssets reads.

For each month, the loop:
  1. Builds live ``Sources`` (HyperSync for debt + balances + position
     balances per the YAML ``sources:`` block, Dune for SSR, RPC for
     ``ilk_rate`` + ERC-4626 convertToAssets). The orchestrator
     upgrades the block resolver to ``DuneBlockResolver`` when
     ``DUNE_API_KEY`` is set, replacing ~25 per-day binary-search RPC
     calls with one Dune query.
  2. Runs ``compute_monthly_pnl(osero, month, sources=...)`` — the
     orchestrator resolves SoM/EoM pin blocks via the block resolver.
  3. Persists ``provenance.json`` + ``summary.md`` + the canonical xlsx
     under ``settlements/osero/<YYYY-MM>/`` via ``write_settlement``.

Required env vars (sourced from ``.env`` via ``set -a; source .env;
set +a``):
    DUNE_API_KEY        — Dune key for the debt / balance / SSR queries
    ETH_RPC             — Ethereum RPC endpoint (archival, for past blocks)

Optional:
    DATABASE_URL        — Postgres raw-data cache (read-through; speeds
                          up re-runs but not required)

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python3 scripts/run_osero_2026.py
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

_OSERO_YAML = _REPO / "config" / "osero.yaml"
_MONTHS = [Month(2026, m) for m in (7, 8)]   # prime effective July 2026


def _selected_months() -> list[Month]:
    """``--months 2026-07[,2026-06]`` narrows the run; default = all.
    Loud on bad/missing/zero-match values — see scripts/_months_arg.py."""
    from _months_arg import filter_by_months
    return filter_by_months(_MONTHS, lambda m: (m.year, m.month))

# Documented in provenance.json so an auditor can see at a glance which
# upstream sources fed each settlement run.
_SOURCES_LIVE = {
    "debt":              "HyperSyncDebtSource",
    "balance":           "HyperSyncBalanceSource",
    "ssr":               "DuneSSRSource",
    "position_balance":  "HyperSyncPositionBalanceSource",
    "convert_to_assets": "RPCConvertToAssetsSource",
    "block_resolver":    "DuneBlockResolver (orchestrator-upgraded) + RPC fallback",
}


def _check_env() -> None:
    """Osero needs only DUNE_API_KEY + ETH_RPC (single-chain prime)."""
    missing = [v for v in ("DUNE_API_KEY", "ETH_RPC") if not os.environ.get(v)]
    if missing:
        print("Missing required env vars:")
        for v in missing:
            print(f"  - {v}")
        print("\nHint: `set -a; source .env; set +a` from the repo root.")
        raise SystemExit(1)


def _check_envio_token(*primes) -> None:
    """Fail fast when a prime's YAML ``sources:`` block resolves any family to
    hypersync but ENVIO_API_TOKEN is missing — otherwise the run burns minutes
    of Dune/RPC work before dying on the first HyperSync fetch."""
    needs = [p.id for p in primes
             if "hypersync" in (getattr(p, "sources", None) or {}).values()]
    if needs and not os.environ.get("ENVIO_API_TOKEN"):
        print(f"Missing ENVIO_API_TOKEN — required by prime(s) {needs} "
              f"(YAML sources: hypersync). Free token: https://app.envio.dev/api-tokens")
        raise SystemExit(1)


def _live_sources() -> Sources:
    """Live sources — every field left ``None`` on purpose.

    ``block_resolver`` left ``None`` so the orchestrator upgrades it to
    ``DuneBlockResolver`` per chain (one Dune query per chain replaces ~25
    binary-search RPC calls/day). ``position_balance`` / ``convert_to_assets``
    are likewise left ``None``: ``compute_monthly_pnl`` merges the prime's
    YAML ``sources:`` overrides into ``None`` fields and defaults any
    still-``None`` field to its registry default. Pinning them to RPC here
    would silently drop a per-prime pilot (e.g. ``position_balance:
    hypersync``) because ``_sources_from_prime`` fills only ``None`` fields."""
    return Sources()


def main() -> int:
    if "--dr-only" in sys.argv:
        # Refresh Distribution Rewards from settle-dr-dune into the existing
        # reports — no recompute (no RPC / Dune). Osero has no tagged DR yet, so
        # this is a fast no-op; the flag still prevents a full recompute.
        from settle.load import refresh_dr_only
        print("OSERO — DR-only refresh from settle-dr-dune (no recompute)")
        refresh_dr_only("osero")
        return 0

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )
    _check_env()

    prime = load_prime(_OSERO_YAML)
    _check_envio_token(prime)

    print("OSERO 2026 settlement")
    print("=" * 110)
    print(f"{'Month':<10} {'prime_agent_total':>21} {'sky_revenue':>17} "
          f"{'sky_direct_shortfall':>22} {'monthly_pnl':>17}")
    print("-" * 110)

    errors: list[tuple[str, str]] = []
    artifacts: list[tuple[str, dict[str, Path]]] = []

    for month in _selected_months():
        label = f"{month.year}-{month.month:02d}"
        try:
            sources = _live_sources()
            result = compute_monthly_pnl(prime, month, sources=sources)
            out_dir = _REPO / "settlements" / "osero" / label
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
