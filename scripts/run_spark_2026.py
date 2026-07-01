"""Spark 2026 multi-month settlement runner.

Single entry point that runs every Spark month in ``_MONTH_PLAN``
(Jan → June 2026). All months replay the same captured fixture set:

  * ``replay/spark_2026_q1/`` (name is historical — it now covers
    Jan → June, extended month-by-month via the capture scripts).

Extending coverage to a new month means advancing the pin blocks in
``config/pin_blocks.yaml``, re-running the Spark capture scripts to
extend the fixtures, and adding a ``_MONTH_PLAN`` entry below.

For each month, the loop:
  1. (Re)loads the right fixture set.
  2. Rebuilds Sources so each ``MockBalanceSource`` gets a fresh
     call-recording slate (avoids leaking state across months). Cat A
     cum_balance is synthesised via RPC at the per-month SoM/EoM blocks.
  3. Runs ``compute_monthly_pnl`` with the month's SoM / EoM pin blocks.
  4. Persists ``provenance.json`` + ``summary.md`` + the canonical xlsx
     under ``settlements/spark/<YYYY-MM>/`` via ``write_settlement``.

This script sets ``SETTLE_SPARK_ALLOW_PRE_PERIOD_ANCHOR=1`` on import so
the Cat B anchor-row check in the fixture loader is bypassed for Spark
venues whose ``cat_b_cum_balance.json`` has no in-period rows. Confirmed
safe (no Q1 flows for those venues, per Spark team) — see
``replay/spark_fixture_loader.py`` for the check itself.

Known limitation: the PSM3 holder-history source pulls Dune query
``7483773`` live, and that query returns HTTP 404 ("Query not found")
today. Fix: capture a PSM3 holder-history fixture or restore the
upstream query.

Run with:
    PYTHONPATH=src python3 scripts/run_spark_2026.py
"""

from __future__ import annotations

import dataclasses
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Bypass the Cat B pre-period-anchor check. See module docstring for why
# this is safe for Spark Q1 today. Must be set BEFORE importing the
# fixture loader so the module-level read sees it.
os.environ.setdefault("SETTLE_SPARK_ALLOW_PRE_PERIOD_ANCHOR", "1")


_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

from settle.compute import compute_monthly_pnl
from settle.domain import Month
from settle.domain.pin_blocks import month_pins
from settle.load import write_settlement
from settle.normalize.registry import get_ssr_source
from replay.spark_fixture_loader import (
    build_spark_sources,
    load_spark_and_fixtures,
)


def _period_dates(year: int, month: int) -> tuple[date, date]:
    """First and last calendar day of (year, month)."""
    period_start = date(year, month, 1)
    first_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return period_start, first_next - timedelta(days=1)


_SETTLEMENT_SOURCES = {
    "debt":             "MockDebtSource backed by replay/spark_2026_q1/debt_timeseries.json",
    "balance":          (
        "Routed MockBalanceSource (Cat B + Cat E from spark_2026_q1 fixtures; "
        "Cat A stubbed; Ethereum `directed_flow` PSM returns empty — mainnet "
        "LITE-PSM is non-custodial for USDS, see PRD §17.11)"
    ),
    "ssr":              "DuneSSRSource (live on-chain read at the period pin block)",
    "position_balance": "RPCPositionBalanceSource",
    "convert_to_assets": "RPCConvertToAssetsSource",
    "psm3":             "RPCPsm3Source (drpc — cached from sky_revenue run)",
    "block_resolver":   "FixtureMultiResolver (date->block from spark_2026_q1 fixtures, RPC fallback)",
    "curve_pool":       "CurvePoolSource",
}

# Pin blocks live in ``config/pin_blocks.yaml`` (single source of truth,
# shared with the capture scripts and the Grove runner). Read via
# ``settle.domain.pin_blocks.month_pins`` below.

# (year, month, fixture_dir). The ``spark_2026_q1`` fixture set was
# refreshed 2026-06-05 to extend lifetime coverage through 2026-05-31:
# debt_timeseries + daily EoD blocks via published Dune queries, Cat B/E
# ``cum_balance`` via per-venue ``transfer_timeseries.sql``, and a new
# ``inflow_by_counterparty.json`` capturing per-day per-counterparty
# token flows for Cat A "raw idle" par-stable venues. So all 5 months
# now run against the same fixture without an in-period anchor gap.
_MONTH_PLAN = [
    (2026, 1, "spark_2026_q1"),
    (2026, 2, "spark_2026_q1"),
    (2026, 3, "spark_2026_q1"),
    (2026, 4, "spark_2026_q1"),
    (2026, 5, "spark_2026_q1"),
    (2026, 6, "spark_2026_q1"),
]


def main() -> int:
    if "--dr-only" in sys.argv:
        # Refresh Distribution Rewards from settle-dr-dune into the existing
        # reports — no recompute (no RPC / Dune). Needs a prior full run.
        from settle.load import refresh_dr_only
        print("Spark — DR-only refresh from settle-dr-dune (no recompute)")
        refresh_dr_only("spark")
        return 0

    print("Spark 2026 settlement (Jan → June)")
    print("=" * 110)
    print(f"{'Month':<10} {'prime_agent_total':>20} {'sky_revenue':>16} "
          f"{'sky_direct_shortfall':>22} {'monthly_pnl':>16}")
    print("-" * 110)

    written_paths: dict[tuple[int, int], dict] = {}
    cached_fixture: str | None = None
    spark = fixtures = None

    for (y, m, fixture_dir) in _MONTH_PLAN:
        if fixture_dir != cached_fixture:
            spark, fixtures = load_spark_and_fixtures(_REPO)
            cached_fixture = fixture_dir

        pins = month_pins(y, m, chains=spark.chains)
        period_start, period_end = _period_dates(y, m)
        sources = build_spark_sources(
            spark, fixtures,
            pin_blocks_som=pins["som"], pin_blocks_eom=pins["eom"],
            period_start=period_start, period_end=period_end,
        )
        # Stop freezing SSR: read it live from chain at the period's pin
        # block instead of the (stale) Q1 fixture snapshot. SSR is a global
        # Sky parameter and a historical on-chain read is deterministic, so
        # this is reproducible while always current — fixing the May 2026
        # mis-pricing where a Q1 fixture's 3.75% was carried into May.
        sources = dataclasses.replace(sources, ssr=get_ssr_source())

        result = compute_monthly_pnl(
            spark, Month(y, m),
            sources=sources,
            pin_blocks_eom=pins["eom"],
            pin_blocks_som=pins["som"],
        )
        # Fold in DR so the console headline matches the written report
        # (write_settlement enriches a copy; this enriches the printed one).
        from settle.load import enrich_with_dr
        result = enrich_with_dr(result)

        label = f"{y}-{m:02d}"
        print(f"{label:<10} ${float(result.prime_agent_total_revenue):>19,.2f} "
              f"${float(result.sky_revenue):>15,.2f} "
              f"${float(result.sky_direct_shortfall):>21,.2f} "
              f"${float(result.monthly_pnl):>15,.2f}")

        out_dir = _REPO / "settlements" / "spark" / label
        written_paths[(y, m)] = write_settlement(
            result, out_dir, sources=_SETTLEMENT_SOURCES,
        )

    print("-" * 110)
    print()
    print("Artifacts written:")
    for (y, m), paths in written_paths.items():
        label = f"{y}-{m:02d}"
        print(f"\n  {label}:")
        for k, p in paths.items():
            print(f"    {k:11s} {p.relative_to(_REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
