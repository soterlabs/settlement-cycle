"""Sync raw extract data to Postgres.

Iterates over every (prime, month) combination and runs
``compute_monthly_pnl`` against the LIVE source registry. The read-through
cache in ``src/settle/extract/cache.py`` writes every fresh fetch to both
the local pickle and Postgres; existing rows are skipped via
``ON CONFLICT (source, args_hash) DO NOTHING``.

Designed to be idempotent: re-running when nothing has changed is a fast
no-op (every key hits the cache). Adding a new venue / oracle / month
introduces new ``(source, args_hash)`` keys → only those are fetched and
inserted.

Required env vars:
    DATABASE_URL         (Postgres target)
    DUNE_API_KEY         (Dune queries)
    ETH_RPC, BASE_RPC, ARBITRUM_RPC, OPTIMISM_RPC,
    UNICHAIN_RPC, AVALANCHE_C_RPC, PLUME_RPC

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python3 scripts/sync_raw_data.py --apply-schema

Optional args:
    --primes obex,grove        Subset of primes (default: all three)
    --months 2026-01,2026-02   Subset of months (default: 2026-01..04)
    --apply-schema             Run db/schema.sql first (idempotent)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

from settle.compute import Sources, compute_monthly_pnl  # noqa: E402
from settle.domain import Month  # noqa: E402
from settle.domain.config import load_prime  # noqa: E402
from settle.extract import postgres_store  # noqa: E402
from settle.normalize.registry import (  # noqa: E402
    get_balance_source,
    get_block_resolver,
    get_convert_to_assets_source,
    get_debt_source,
    get_position_balance_source,
    get_psm3_source,
    get_ssr_source,
)

_PRIMES = {
    "obex":  _REPO / "config" / "obex.yaml",
    "grove": _REPO / "config" / "grove.yaml",
    "spark": _REPO / "config" / "spark.yaml",
}
_DEFAULT_MONTHS = [Month(2026, m) for m in (1, 2, 3, 4)]


def _live_sources() -> Sources:
    return Sources(
        debt=get_debt_source(),
        balance=get_balance_source(),
        ssr=get_ssr_source(),
        position_balance=get_position_balance_source(),
        convert_to_assets=get_convert_to_assets_source(),
        psm3=get_psm3_source(),
        block_resolver=get_block_resolver(),
    )


def _count_rows() -> int | None:
    """Total rows in ``raw_data``; None if Postgres is unavailable."""
    conn = postgres_store._get_conn()
    if conn is None:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw_data")
        row = cur.fetchone()
    return int(row[0]) if row else None


def _required_env() -> list[str]:
    return [
        "DATABASE_URL", "DUNE_API_KEY",
        "ETH_RPC", "BASE_RPC", "ARBITRUM_RPC", "OPTIMISM_RPC",
        "UNICHAIN_RPC", "AVALANCHE_C_RPC", "PLUME_RPC",
    ]


def _parse_months(s: str | None) -> list[Month]:
    if not s:
        return _DEFAULT_MONTHS
    out: list[Month] = []
    for tok in s.split(","):
        y, m = tok.strip().split("-")
        out.append(Month(int(y), int(m)))
    return out


def _parse_primes(s: str | None) -> list[str]:
    if not s:
        return list(_PRIMES)
    primes = [t.strip() for t in s.split(",")]
    bad = [p for p in primes if p not in _PRIMES]
    if bad:
        raise SystemExit(f"Unknown prime(s): {bad}. Choose from {list(_PRIMES)}")
    return primes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primes", default=None,
                        help="comma-separated subset of " + ",".join(_PRIMES))
    parser.add_argument("--months", default=None,
                        help="comma-separated YYYY-MM (default 2026-01..04)")
    parser.add_argument("--apply-schema", action="store_true",
                        help="Apply db/schema.sql before sync (idempotent CREATE IF NOT EXISTS).")
    args = parser.parse_args()

    missing = [v for v in _required_env() if not os.environ.get(v)]
    if missing:
        print("Missing required env vars:")
        for v in missing:
            print(f"  - {v}")
        print("\nHint: `set -a; source .env; set +a` from the repo root.")
        return 1

    if args.apply_schema:
        schema_path = _REPO / "db" / "schema.sql"
        print(f"Applying schema from {schema_path.relative_to(_REPO)}")
        postgres_store.apply_schema(schema_path.read_text())

    if not postgres_store.is_enabled():
        print("Postgres unreachable — check DATABASE_URL.")
        return 1

    primes = _parse_primes(args.primes)
    months = _parse_months(args.months)

    rows_before = _count_rows()
    print(f"raw_data rows before sync: {rows_before}")
    print(f"Scope: {len(primes)} prime(s) x {len(months)} month(s) "
          f"= {len(primes) * len(months)} cell(s)")
    print(f"  primes: {primes}")
    print(f"  months: {[f'{m.year}-{m.month:02d}' for m in months]}")
    print()

    errors: list[tuple[str, str, str]] = []
    for prime_id in primes:
        prime = load_prime(_PRIMES[prime_id])
        for month in months:
            label = f"{month.year}-{month.month:02d}"
            tag = f"{prime_id.upper()} {label}"
            print(f"  {tag} ...", end="", flush=True)
            try:
                compute_monthly_pnl(prime, month, sources=_live_sources())
                print(" ok", flush=True)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                print(f" FAILED: {msg}", flush=True)
                errors.append((prime_id, label, msg))

    rows_after = _count_rows()
    delta = (rows_after or 0) - (rows_before or 0)
    print()
    print(f"raw_data rows after sync:  {rows_after}  (Δ +{delta})")
    if errors:
        print(f"\n{len(errors)} cell(s) failed:")
        for p, m, msg in errors:
            print(f"  {p} {m}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
