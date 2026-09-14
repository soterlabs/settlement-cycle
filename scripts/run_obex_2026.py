"""Live monthly settlement runner using configured HyperSync event sources.

Requires ENVIO_API_TOKEN and archival ETH_RPC. DATABASE_URL enables the
reusable raw-data cache. Dune is retained as a separate comparison oracle;
these migrated primes do not require a Dune API key to produce settlements.
RPC still supplies contract state and valuation reads. Writes canonical
settlement artifacts under settlements/<prime>/<month>/.
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
from settle.normalize.registry import resolved_source_labels  # noqa: E402

_OBEX_YAML = _REPO / "config" / "obex.yaml"
_MONTHS = [Month(2026, m) for m in (1, 2, 3, 4, 5, 6, 7, 8)]


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
    "ssr":               "HyperSyncSSRSource",
    "position_balance":  "HyperSyncPositionBalanceSource",
    "convert_to_assets": "RPCConvertToAssetsSource",
    "block_resolver":    "HyperSyncBlockResolver",
}


def _check_env() -> None:
    """OBEX needs ENVIO_API_TOKEN + ETH_RPC (single-chain prime)."""
    missing = [v for v in ("ENVIO_API_TOKEN", "ETH_RPC") if not os.environ.get(v)]
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
    """Resolve live sources from the prime config; preserve caller overrides."""
    return Sources()


def main() -> int:
    if "--dr-only" in sys.argv:
        # Refresh Distribution Rewards from settle-dr-dune into the existing
        # reports — no recompute (no RPC / Dune). obex has no tagged DR, so
        # this is a fast no-op; the flag still prevents a full recompute.
        from settle.load import refresh_dr_only
        from _months_arg import requested_months
        print("OBEX — DR-only refresh from settle-dr-dune (no recompute)")
        refresh_dr_only("obex", months=requested_months())
        return 0

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )
    _check_env()

    prime = load_prime(_OBEX_YAML)
    _check_envio_token(prime)

    print("OBEX 2026 multi-month settlement")
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
            out_dir = _REPO / "settlements" / "obex" / label
            paths = write_settlement(result, out_dir, sources=resolved_source_labels(prime, _SOURCES_LIVE))
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
