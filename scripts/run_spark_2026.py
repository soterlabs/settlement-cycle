"""Spark 2026 multi-month settlement runner.

Single entry point that runs every Spark month for which a fixture set
currently exists (Jan-May 2026, all from ``tests/fixtures/spark_2026_q1/``
after the 2026-06-05 fixture refresh). Extending coverage means
refreshing the Spark fixture set (debt timeseries, Cat B/E cum_balance,
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

The Cat B pre-period-anchor check in the fixture loader is bypassed ONLY
for the months where "no mid-period flows" was actually verified with the
Spark team (Q1 2026 — see ``_ANCHOR_CHECK_VERIFIED_MONTHS``). Later
months run with the check armed: a Cat B venue holding a material
balance with no in-period fixture rows fails loud instead of silently
booking deposit principal as yield. Verify a new month before adding it
to the set.

Known limitation: the PSM3 holder-history source pulls Dune query
``7483773`` live, and that query returns HTTP 404 ("Query not found")
today. Fix: capture a PSM3 holder-history fixture or restore the
upstream query.

Run with:
    PYTHONPATH=src python3 scripts/run_spark_2026.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Months where the Cat B pre-period-anchor check may be bypassed — i.e.
# where "no mid-period flows for the anchor-only venues" was explicitly
# verified with the Spark team. The bypass is applied per-month inside
# the run loop (the loader reads the env var at build_spark_sources
# time), NOT globally: a global setdefault would silently disable the
# guard for every future month this runner grows to cover.
_ANCHOR_CHECK_VERIFIED_MONTHS = {(2026, 1), (2026, 2), (2026, 3)}

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

from settle.compute import compute_monthly_pnl
from settle.domain import Chain, Month
from settle.load import write_settlement
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
    "ssr":              "MockSSRSource (reused from grove_2026_03 — Sky-wide)",
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
]


def main() -> int:
    print("Spark 2026 multi-month settlement (Jan → May)")
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

        pins = PIN_BLOCKS_BY_MONTH[(y, m)]
        period_start, period_end = _period_dates(y, m)
        if (y, m) in _ANCHOR_CHECK_VERIFIED_MONTHS:
            os.environ["SETTLE_SPARK_ALLOW_PRE_PERIOD_ANCHOR"] = "1"
        else:
            os.environ.pop("SETTLE_SPARK_ALLOW_PRE_PERIOD_ANCHOR", None)
        sources = build_spark_sources(
            spark, fixtures,
            pin_blocks_som=pins["som"], pin_blocks_eom=pins["eom"],
            period_start=period_start, period_end=period_end,
        )

        result = compute_monthly_pnl(
            spark, Month(y, m),
            sources=sources,
            pin_blocks_eom=pins["eom"],
            pin_blocks_som=pins["som"],
        )

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
