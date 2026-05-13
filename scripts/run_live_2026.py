"""Live multi-month settlement for OBEX + Spark + Grove (Jan–Apr 2026).

Reads raw data from the Postgres ``raw_data`` table (set via ``DATABASE_URL``)
when present, falls back to live Dune + RPC otherwise. The on-disk pickle cache
in ``~/.cache/msc-settle`` is checked first.

For each (prime, month):
  1. Build live ``Sources``. ``block_resolver`` is left ``None`` so the
     orchestrator can upgrade to ``DuneBlockResolver`` (one Dune query per
     chain replaces ~25 RPC binary-search calls per day).
  2. ``compute_monthly_pnl`` resolves pin blocks itself.
  3. Write ``settlements/<prime>/<YYYY-MM>/`` artifacts.
  4. Track headline numbers for a final summary table.

Required env vars:
    DATABASE_URL        — Postgres connection (read-through cache)
    DUNE_API_KEY        — fallback / fresh fetches
    ETH_RPC, BASE_RPC, ARBITRUM_RPC, OPTIMISM_RPC, UNICHAIN_RPC,
    AVALANCHE_C_RPC, PLUME_RPC

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python3 -u scripts/run_live_2026.py

Optional subsets:
    --primes grove,spark
    --months 2026-03,2026-04
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
from settle.normalize.registry import (  # noqa: E402
    get_balance_source,
    get_convert_to_assets_source,
    get_debt_source,
    get_position_balance_source,
    get_ssr_source,
)

_PRIMES = {
    "obex":  _REPO / "config" / "obex.yaml",
    "grove": _REPO / "config" / "grove.yaml",
    "spark": _REPO / "config" / "spark.yaml",
}
_MONTHS = [Month(2026, m) for m in (1, 2, 3, 4)]

_SOURCES_LIVE = {
    "debt":              "DuneDebtSource",
    "balance":           "DuneBalanceSource",
    "ssr":               "DuneSSRSource",
    "position_balance":  "RPCPositionBalanceSource",
    "convert_to_assets": "RPCConvertToAssetsSource",
    # ``psm3`` is left None in ``_live_sources()`` so the orchestrator
    # upgrades it to ``DunePsm3Source`` when ``DUNE_API_KEY`` is set; that
    # source bulk-loads share + reserve histories from Dune and falls back
    # to ``RPCPsm3Source`` only on Dune failure. Recorded here so the
    # settlement-artifact provenance matches what actually ran.
    "psm3":              "DunePsm3Source (orchestrator-upgraded) + RPCPsm3Source fallback",
    # Cat C / D off-pool rewards (Merkl-style aToken drops). Activated when
    # ``prime.external_alm_sources[venue.chain]`` is non-empty; queries
    # Dune ``tokens.transfers`` per (chain, token, sender). Currently active
    # only for Grove on Ethereum (Merkl distributor → aHorRwaRLUSD + aEthRLUSD).
    "atoken_external_rewards": "DuneExternalInflow via _atoken_external_revenue_usd (Cat C/D only)",
    "block_resolver":    "DuneBlockResolver (orchestrator-upgraded) + RPC fallback",
    "curve_pool":        "CurvePoolSource (lazy)",
    "v3_position":       "DuneV3InflowSource (orchestrator-upgraded) + RPC fallback",
}


def _required_env() -> list[str]:
    return [
        "DUNE_API_KEY",
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


def _live_sources() -> Sources:
    """Live sources with intentional ``None`` defaults for the sources whose
    Dune-backed variant the orchestrator picks based on ``DUNE_API_KEY``:

      * ``block_resolver`` → upgraded to ``DuneBlockResolver`` per chain.
      * ``psm3``           → upgraded to ``DunePsm3Source`` for Spark L2 PSMs.
      * ``v3_position``    → upgraded to ``DuneV3InflowSource`` for V3 events.

    Passing concrete RPC sources here would short-circuit those upgrades.
    """
    return Sources(
        debt=get_debt_source(),
        balance=get_balance_source(),
        ssr=get_ssr_source(),
        position_balance=get_position_balance_source(),
        convert_to_assets=get_convert_to_assets_source(),
    )


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
                write_settlement(result, out_dir, sources=_SOURCES_LIVE)
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
