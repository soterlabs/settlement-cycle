"""Spark 2026 multi-month settlement runner.

Single entry point that runs every Spark month for which a fixture set
currently exists. Today that's Q1 only:

  * Jan / Feb / Mar → ``tests/fixtures/spark_2026_q1/``.

Apr+ are not yet runnable from this script — extending coverage means
capturing a new Spark fixture set (debt timeseries, Cat B/E cum_balance,
L2 block resolvers) for the relevant months and adding entries to
``PIN_BLOCKS_BY_MONTH`` + ``_MONTH_PLAN`` below.

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
``tests/fixtures/spark_fixture_loader.py`` for the check itself.

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
from settle.domain import Chain, Month
from settle.load import write_settlement
from settle.normalize.registry import get_ssr_source
from tests.fixtures.spark_fixture_loader import (
    build_spark_sources,
    load_spark_and_fixtures,
)


def _period_dates(year: int, month: int) -> tuple[date, date]:
    """First and last calendar day of (year, month)."""
    period_start = date(year, month, 1)
    first_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return period_start, first_next - timedelta(days=1)


_SETTLEMENT_SOURCES = {
    "debt":             "MockDebtSource backed by tests/fixtures/spark_2026_q1/debt_timeseries.json",
    "balance":          (
        "Routed MockBalanceSource (Cat B + Cat E from spark_2026_q1 fixtures; "
        "Cat A stubbed; Ethereum `directed_flow` PSM returns empty — mainnet "
        "LITE-PSM is non-custodial for USDS, see PRD §17.11)"
    ),
    "ssr":              "DuneSSRSource (live Dune query of on-chain SSR events at the period pin block)",
    "position_balance": "RPCPositionBalanceSource",
    "convert_to_assets": "RPCConvertToAssetsSource",
    "psm3":             "RPCPsm3Source (drpc — cached from sky_revenue run)",
    "block_resolver":   "FixtureMultiResolver (date->block from spark_2026_q1 fixtures, RPC fallback)",
    "curve_pool":       "CurvePoolSource",
}

# Pin blocks per (month, chain). Eth + Base from Grove's fixtures
# (verified); Arb/Op/Uni from Dune query 7401735; Avalanche-C from Dune
# query 7402172.
PIN_BLOCKS_BY_MONTH = {
    (2026, 1): {
        "som": {Chain.ETHEREUM: 24136052, Chain.BASE: 40218126,
                Chain.ARBITRUM: 416593973, Chain.OPTIMISM: 145813411,
                Chain.UNICHAIN: 36477240, Chain.AVALANCHE_C: 74824633},
        "eom": {Chain.ETHEREUM: 24358292, Chain.BASE: 41557326,
                Chain.ARBITRUM: 427315178, Chain.OPTIMISM: 147152611,
                Chain.UNICHAIN: 39155640, Chain.AVALANCHE_C: 76986991},
    },
    (2026, 2): {
        "som": {Chain.ETHEREUM: 24358292, Chain.BASE: 41557326,
                Chain.ARBITRUM: 427315178, Chain.OPTIMISM: 147152611,
                Chain.UNICHAIN: 39155640, Chain.AVALANCHE_C: 76986991},
        "eom": {Chain.ETHEREUM: 24558867, Chain.BASE: 42766926,
                Chain.ARBITRUM: 437025050, Chain.OPTIMISM: 148362211,
                Chain.UNICHAIN: 41574840, Chain.AVALANCHE_C: 79250451},
    },
    (2026, 3): {
        "som": {Chain.ETHEREUM: 24558867, Chain.BASE: 42766926,
                Chain.ARBITRUM: 437025050, Chain.OPTIMISM: 148362211,
                Chain.UNICHAIN: 41574840, Chain.AVALANCHE_C: 79250451},
        "eom": {Chain.ETHEREUM: 24781026, Chain.BASE: 44106126,
                Chain.ARBITRUM: 447736930, Chain.OPTIMISM: 149701411,
                Chain.UNICHAIN: 44253240, Chain.AVALANCHE_C: 81789468},
    },
    # Apr/May added 2026-06-05. Eth+Base+Avalanche-C blocks copied from
    # Grove's Apr/May fixtures; Arb/Op/Uni resolved via RPC binary search
    # (Dune credits exhausted before fixture refresh).
    (2026, 4): {
        "som": {Chain.ETHEREUM: 24781026, Chain.BASE: 44106126,
                Chain.ARBITRUM: 447736930, Chain.OPTIMISM: 149701411,
                Chain.UNICHAIN: 44253240, Chain.AVALANCHE_C: 81789468},
        "eom": {Chain.ETHEREUM: 24996367, Chain.BASE: 45402126,
                Chain.ARBITRUM: 458085623, Chain.OPTIMISM: 150997411,
                Chain.UNICHAIN: 46845240, Chain.AVALANCHE_C: 84298393},
    },
    (2026, 5): {
        "som": {Chain.ETHEREUM: 24996367, Chain.BASE: 45402126,
                Chain.ARBITRUM: 458085623, Chain.OPTIMISM: 150997411,
                Chain.UNICHAIN: 46845240, Chain.AVALANCHE_C: 84298393},
        "eom": {Chain.ETHEREUM: 25218797, Chain.BASE: 46741326,
                Chain.ARBITRUM: 468748167, Chain.OPTIMISM: 152336611,
                Chain.UNICHAIN: 49523640, Chain.AVALANCHE_C: 86865826},
    },
    # June added 2026-07-03: EoM = June 30 EoD blocks from the refreshed
    # daily_eod_blocks fixtures (extend_spark_fixtures.py, Dune 7474490).
    (2026, 6): {
        "som": {Chain.ETHEREUM: 25218797, Chain.BASE: 46741326,
                Chain.ARBITRUM: 468748167, Chain.OPTIMISM: 152336611,
                Chain.UNICHAIN: 49523640, Chain.AVALANCHE_C: 86865826},
        "eom": {Chain.ETHEREUM: 25433938, Chain.BASE: 48037326,
                Chain.ARBITRUM: 479089705, Chain.OPTIMISM: 153632611,
                Chain.UNICHAIN: 52115640, Chain.AVALANCHE_C: 89166730},
    },
    # July added 2026-08-03: EoM = July 31 EoD blocks resolved via
    # HyperSync binary search (June-30 re-derivation matched the June pins
    # on every chain). ROBINHOOD pins cover the new S63 spUSDG venue
    # (som = EoD Jun 30 block 653324, eom = EoD Jul 31 block 24591562).
    (2026, 7): {
        "som": {Chain.ETHEREUM: 25433938, Chain.BASE: 48037326,
                Chain.ARBITRUM: 479089705, Chain.OPTIMISM: 153632611,
                Chain.UNICHAIN: 52115640, Chain.AVALANCHE_C: 89166730,
                Chain.ROBINHOOD: 653324},
        "eom": {Chain.ETHEREUM: 25656292, Chain.BASE: 49376526,
                Chain.ARBITRUM: 489802913, Chain.OPTIMISM: 154971811,
                Chain.UNICHAIN: 54794040, Chain.AVALANCHE_C: 91716609,
                Chain.ROBINHOOD: 24591562},
    },
}

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
    (2026, 7, "spark_2026_q1"),
]


def _selected_plan() -> list[tuple[int, int, str]]:
    """``--months 2026-07[,2026-06]`` narrows the run; default = all."""
    if "--months" in sys.argv:
        raw = sys.argv[sys.argv.index("--months") + 1]
        want = {tuple(int(x) for x in p.split("-")) for p in raw.split(",")}
        return [e for e in _MONTH_PLAN if (e[0], e[1]) in want]
    return _MONTH_PLAN


def main() -> int:
    if "--dr-only" in sys.argv:
        # Refresh Distribution Rewards from settle-dr-dune into the existing
        # reports — no recompute (no RPC / Dune). Needs a prior full run.
        from settle.load import refresh_dr_only
        print("Spark — DR-only refresh from settle-dr-dune (no recompute)")
        refresh_dr_only("spark")
        return 0

    print("Spark 2026 multi-month settlement (Jan → Jun)")
    print("=" * 110)
    print(f"{'Month':<10} {'prime_agent_total':>20} {'sky_revenue':>16} "
          f"{'sky_direct_shortfall':>22} {'monthly_pnl':>16}")
    print("-" * 110)

    written_paths: dict[tuple[int, int], dict] = {}
    cached_fixture: str | None = None
    spark = fixtures = None

    for (y, m, fixture_dir) in _selected_plan():
        if fixture_dir != cached_fixture:
            spark, fixtures = load_spark_and_fixtures(_REPO)
            cached_fixture = fixture_dir

        pins = PIN_BLOCKS_BY_MONTH[(y, m)]
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
