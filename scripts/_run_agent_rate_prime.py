"""Shared 2026 multi-month settlement runner for agent-rate-only primes.

Keel and Skybase have no allocator ilk and no supply-side venues — their
settlement reduces to the agent rate (SSR + 20bps) on subproxy treasury
holdings. Everything else (debt, sky_revenue, venue revenue) is zero by
construction (see ``Prime.ilk_bytes32 is None`` handling in
``normalize/debt.py``).

The monthly loop mirrors ``run_obex_2026.py``: live Dune + RPC sources,
``compute_monthly_pnl`` per month, artifacts (provenance.json + summary.md
+ xlsx) under ``settlements/<prime>/<YYYY-MM>/`` via ``write_settlement``.

Required env vars (sourced from ``.env``):
    DUNE_API_KEY        — Dune key for balance / SSR queries
    ETH_RPC             — Ethereum RPC endpoint (archival, for past blocks)

Run via the per-prime entry points:
    set -a; source .env; set +a
    PYTHONPATH=src python3 scripts/run_keel_2026.py
    PYTHONPATH=src python3 scripts/run_skybase_2026.py
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

_MONTHS = [Month(2026, m) for m in (1, 2, 3, 4, 5, 6, 7)]


def _selected_months() -> list[Month]:
    """``--months 2026-07[,2026-06]`` narrows the run; default = all.
    Loud on bad/missing/zero-match values — see scripts/_months_arg.py."""
    from _months_arg import filter_by_months
    return filter_by_months(_MONTHS, lambda m: (m.year, m.month))

# Documented in provenance.json so an auditor can see at a glance which
# upstream sources fed each settlement run. The debt source is listed for
# completeness but is never queried (no ilk).
_SOURCES_LIVE = {
    "debt":              "HyperSyncDebtSource (unused — agent-rate-only prime, no ilk)",
    "balance":           "HyperSyncBalanceSource",
    "ssr":               "DuneSSRSource",
    "position_balance":  "HyperSyncPositionBalanceSource",
    "convert_to_assets": "RPCConvertToAssetsSource",
    "block_resolver":    "DuneBlockResolver (orchestrator-upgraded) + RPC fallback",
}


def _check_env() -> None:
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

    ``compute_monthly_pnl`` merges each prime's YAML ``sources:`` overrides
    into the ``None`` fields (``_sources_from_prime``) and defaults any
    still-``None`` field to its registry default at each call site. Passing a
    concrete source here would short-circuit the orchestrator's
    ``block_resolver`` Dune upgrade and silently drop a prime's per-prime
    backend pilot for that field (``_sources_from_prime`` fills only ``None``
    fields, so a non-``None`` ``position_balance`` would make
    ``position_balance: hypersync`` in a prime YAML a no-op)."""
    return Sources()


def run(prime_id: str) -> int:
    if "--dr-only" in sys.argv:
        # Refresh Distribution Rewards from settle-dr-dune into the existing
        # reports — no recompute (no RPC / Dune). Needs a prior full run.
        from settle.load import refresh_dr_only
        print(f"{prime_id.upper()} — DR-only refresh from settle-dr-dune (no recompute)")
        refresh_dr_only(prime_id)
        return 0

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )
    _check_env()

    prime = load_prime(_REPO / "config" / f"{prime_id}.yaml")
    _check_envio_token(prime)

    print(f"{prime_id.upper()} 2026 multi-month settlement — agent-rate-only")
    print("=" * 96)
    print(f"{'Month':<10} {'agent_rate':>16} {'prime_agent_total':>21} "
          f"{'sky_revenue':>15} {'monthly_pnl':>15}")
    print("-" * 96)

    errors: list[tuple[str, str]] = []
    artifacts: list[tuple[str, dict[str, Path]]] = []

    for month in _selected_months():
        label = f"{month.year}-{month.month:02d}"
        try:
            result = compute_monthly_pnl(prime, month, sources=_live_sources())
            # Fold in DR so the console headline matches the written report
            # (write_settlement enriches a copy; this enriches the printed one).
            from settle.load import enrich_with_dr
            result = enrich_with_dr(result)
            out_dir = _REPO / "settlements" / prime_id / label
            paths = write_settlement(result, out_dir, sources=_SOURCES_LIVE)
            artifacts.append((label, paths))
            print(
                f"{label:<10} "
                f"${float(result.agent_rate):>15,.2f} "
                f"${float(result.prime_agent_total_revenue):>20,.2f} "
                f"${float(result.sky_revenue):>14,.2f} "
                f"${float(result.monthly_pnl):>14,.2f}",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001 — keep going across months
            msg = f"{type(e).__name__}: {e}"
            errors.append((label, msg))
            print(f"{label:<10}  ✗ FAILED: {msg}", flush=True)
            traceback.print_exc()

    print("-" * 96)
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
