"""Fresh monthly settlements for Obex, Grove and Spark using HyperSync.

Configured HyperSync sources supply event histories, SSR and block boundaries.
Archival RPC supplies contract-state and valuation reads. DATABASE_URL enables
the incremental raw-data cache; the local extraction cache is checked first.
Dune API credentials are needed only by the separate comparison tools.

Writes canonical reports under settlements/<prime>/<month>/.

Example:
    .venv/bin/python scripts/run_live_2026.py --primes grove,spark --months 2026-08

Required: ENVIO_API_TOKEN and the RPC endpoints listed in _required_env().
DATABASE_URL is optional, and avoids repeating historical raw-log scans.
"""

from __future__ import annotations

import argparse
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

_PRIMES = {
    "obex":  _REPO / "config" / "obex.yaml",
    "grove": _REPO / "config" / "grove.yaml",
    "spark": _REPO / "config" / "spark.yaml",
}
_MONTHS = [Month(2026, m) for m in (1, 2, 3, 4)]

_SOURCES_LIVE = {
    "debt":              "HyperSyncDebtSource",
    "balance":           "HyperSyncBalanceSource",
    "ssr":               "HyperSyncSSRSource",
    "position_balance":  "RPCPositionBalanceSource",
    "convert_to_assets": "RPCConvertToAssetsSource",
    # Each configured PSM3 contract has independent Dune parity evidence.
    "psm3":              "HyperSyncPsm3Source (monthly opening RPC anchors)",
    "atoken_external_rewards": (
        "HyperSync venue events: Merkl Claimed/Mint joins and direct receipts; "
        "ERC-20 Transfer receipts for other configured senders"
    ),
    "block_resolver":    "HyperSyncBlockResolver",
    "curve_pool":        "CurvePoolSource (lazy)",
    "v4_position":       "HyperSyncV4PositionSource (per-venue routing)",
    "v3_position":       "HyperSyncV3PositionSource (per-venue routing)",
}


def _required_env() -> list[str]:
    return [
        "ENVIO_API_TOKEN",
        "ETH_RPC", "BASE_RPC", "ARBITRUM_RPC", "OPTIMISM_RPC",
        "UNICHAIN_RPC", "AVALANCHE_C_RPC", "PLUME_RPC",
    ]


def _check_env() -> None:
    missing = [v for v in _required_env() if not os.environ.get(v)]
    if missing:
        print("Missing required env vars:")
        for v in missing:
            print(f"  - {v}")
        print("\nHint: `set -a; source .env; set +a` from the repo root.")
        raise SystemExit(1)
    if not os.environ.get("DATABASE_URL"):
        print("Note: DATABASE_URL not set — Postgres cache layer disabled.")

def _check_envio_token(*primes) -> None:
    """Fail fast when a prime's YAML ``sources:`` block resolves any family to
    hypersync but ENVIO_API_TOKEN is missing, before any extraction starts."""
    needs = [p.id for p in primes
             if "hypersync" in (getattr(p, "sources", None) or {}).values()]
    if needs and not os.environ.get("ENVIO_API_TOKEN"):
        print(f"Missing ENVIO_API_TOKEN — required by prime(s) {needs} "
              f"(YAML sources: hypersync). Free token: https://app.envio.dev/api-tokens")
        raise SystemExit(1)



def _live_sources() -> Sources:
    """Resolve configured sources; retain explicit caller/fixture overrides."""
    return Sources()


def _parse_months(s: str | None) -> list[Month]:
    if not s:
        return _MONTHS
    out: list[Month] = []
    for tok in s.split(","):
        y, m = tok.strip().split("-")
        out.append(Month(int(y), int(m)))
    return out


def _parse_primes(s: str | None) -> list[str]:
    if not s:
        return list(_PRIMES.keys())
    primes = [t.strip() for t in s.split(",")]
    bad = [p for p in primes if p not in _PRIMES]
    if bad:
        print(f"Unknown prime(s): {bad}. Choose from {list(_PRIMES.keys())}")
        raise SystemExit(1)
    return primes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primes", default=None,
                        help="comma-separated subset of " + ",".join(_PRIMES))
    parser.add_argument("--months", default=None,
                        help="comma-separated YYYY-MM (default 2026-01..04)")
    parser.add_argument("--log-level", default="INFO",
                        help="Python logging level (default INFO)")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )

    _check_env()
    primes = _parse_primes(args.primes)
    months = _parse_months(args.months)

    total_cells = len(primes) * len(months)
    print(f"Live runner: {len(primes)} prime(s) × {len(months)} month(s) = {total_cells} run(s).")
    print(f"  primes: {primes}")
    print(f"  months: {[f'{m.year}-{m.month:02d}' for m in months]}")
    print()

    headline: dict[tuple[str, str], dict] = {}
    errors: list[tuple[str, str, str]] = []
    idx = 0

    for prime_id in primes:
        prime = load_prime(_PRIMES[prime_id])
        _check_envio_token(prime)
        for month in months:
            idx += 1
            label = f"{month.year}-{month.month:02d}"
            tag = f"{prime_id.upper()} {label}"
            print(f"\n==== {tag} ({idx}/{total_cells}) ====", flush=True)
            try:
                sources = _live_sources()
                result = compute_monthly_pnl(prime, month, sources=sources)
                headline[(prime_id, label)] = {
                    "prime_agent_revenue":  float(result.prime_agent_revenue),
                    "agent_rate":           float(result.agent_rate),
                    "sky_revenue":          float(result.sky_revenue),
                    "monthly_pnl":          float(result.monthly_pnl),
                    "sky_direct_shortfall": float(result.sky_direct_shortfall),
                }
                out_dir = _REPO / "settlements" / prime_id / label
                write_settlement(result, out_dir,
                                 sources=resolved_source_labels(prime, _SOURCES_LIVE))
                print(
                    f"  prime_agent_revenue: ${float(result.prime_agent_revenue):>18,.2f}\n"
                    f"  agent_rate:          ${float(result.agent_rate):>18,.2f}\n"
                    f"  sky_revenue:         ${float(result.sky_revenue):>18,.2f}\n"
                    f"  monthly_pnl:         ${float(result.monthly_pnl):>18,.2f}",
                    flush=True,
                )
            except Exception as e:  # noqa: BLE001 — keep going across the matrix
                msg = f"{type(e).__name__}: {e}"
                errors.append((prime_id, label, msg))
                print(f"  ✗ FAILED: {msg}", flush=True)
                traceback.print_exc()

    # Summary
    print()
    print("=" * 110)
    print("SUMMARY — 2026 live revenue")
    print("=" * 110)
    print(f"{'prime':<6} {'month':<8} {'prime_agent_revenue':>22} {'agent_rate':>14} "
          f"{'sky_revenue':>16} {'monthly_pnl':>16}")
    print("-" * 110)
    for prime_id in primes:
        for month in months:
            label = f"{month.year}-{month.month:02d}"
            d = headline.get((prime_id, label))
            if d is None:
                print(f"{prime_id:<6} {label:<8}  {'— FAILED —':>22}")
                continue
            print(
                f"{prime_id:<6} {label:<8} "
                f"${d['prime_agent_revenue']:>21,.2f} "
                f"${d['agent_rate']:>13,.2f} "
                f"${d['sky_revenue']:>15,.2f} "
                f"${d['monthly_pnl']:>15,.2f}"
            )
    print("=" * 110)

    if errors:
        print()
        print(f"{len(errors)} cell(s) failed:")
        for p, m, msg in errors:
            print(f"  {p} {m}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
