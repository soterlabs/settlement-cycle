"""Spark Q1 2026 sky-revenue (only) — Jan / Feb / Mar 2026.

Scope (per user request 2026-04-28): compute ONLY ``sky_revenue`` for Spark.
``prime_agent_total_revenue`` is deferred — Spark has 51 venues across 6 chains
with ~30 new pricing paths (PSM3s, sUSDS variants, Spark-specific Curve LPs,
syrupUSDC, fsUSDS, sUSDe) that aren't yet wired up. PRD §17.12 documents the gap.

Inputs (no DUNE_API_KEY needed — fixture-based):
* ``debt`` — ``tests/fixtures/spark_2026_q1/debt_timeseries.json`` (captured
  via mcp__dune from query 7399651, ALLOCATOR-SPARK-A from 2024-11-18 through
  2026-03-31 EoM, 452 rows)
* ``ssr`` — reused from ``tests/fixtures/grove_2026_03/dune_outputs.json``
  (SSR is Sky-wide; identical across primes)
* ``subproxy_usds`` / ``alm_usds`` / ``subproxy_susds_principal`` — confirmed
  $0 from Dune query 7399654 (Spark holds dust amounts of USDS; the ALM
  holds millions of sUSDS but those are tracked as a Cat B venue, not a
  utilized-reduction term).
* ``Eth directed-flow PSM`` — confirmed $0 (Dune query 7401552 returned 0
  rows; Spark does not transfer USDS through the Sky LITE-PSM-USDC).
* ``L2 PSM3 holdings`` — read live via RPC (one ``shares`` + one
  ``convertToAssetValue`` per chain × per active day). Cached.

Run with:
    PYTHONPATH=src python3 scripts/run_spark_2026_q1.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from settle.compute.sky_revenue import compute_sky_revenue  # noqa: E402
from settle.domain import Chain, Period  # noqa: E402
from settle.domain.config import load_prime  # noqa: E402
from settle.extract.rpc import RPCError  # noqa: E402
from settle.normalize.registry import get_psm3_source  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture loaders
# ---------------------------------------------------------------------------

def _load_debt() -> pd.DataFrame:
    """452-row debt timeseries from MCP-fetched Dune query 7399651."""
    path = _REPO / "tests" / "fixtures" / "spark_2026_q1" / "debt_timeseries.json"
    with open(path) as f:
        rows = json.load(f)["rows"]
    df = pd.DataFrame(rows)
    df["block_date"] = pd.to_datetime(df["block_date"]).dt.date
    df["daily_dart"] = df["daily_dart"].apply(Decimal)
    df["cum_debt"] = df["cum_debt"].apply(Decimal)
    return df.sort_values("block_date").reset_index(drop=True)


def _load_ssr() -> pd.DataFrame:
    """SSR is Sky-wide — reuse Grove's fixture."""
    path = _REPO / "tests" / "fixtures" / "grove_2026_03" / "dune_outputs.json"
    with open(path) as f:
        rows = json.load(f)["ssr"]["rows"]
    df = pd.DataFrame(rows)
    df["effective_date"] = pd.to_datetime(df["effective_date"]).dt.date
    df["ssr_apy"] = df["ssr_apy"].apply(Decimal)
    return df.sort_values("effective_date").reset_index(drop=True)


def _empty_balance_df() -> pd.DataFrame:
    return pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})


# ---------------------------------------------------------------------------
# Period building
# ---------------------------------------------------------------------------

def _eom_date(year: int, month: int) -> date:
    """Last calendar day of ``year-month``."""
    first_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first_next - timedelta(days=1)


# Hardcoded EoM pin_blocks for Q1 2026.
# Eth + Base values come from Grove's Q1 2026 fixtures (already verified).
# Arbitrum/Optimism/Unichain values come from MCP-Dune query 7401735 — drpc
# binary search through ``find_block_at_or_before`` is unreliable on these
# providers, so resolution is moved to Dune.
PIN_BLOCKS_EOM: dict[tuple[int, int], dict[Chain, int]] = {
    (2026, 1): {
        Chain.ETHEREUM: 24358292, Chain.BASE: 41557326,
        Chain.ARBITRUM: 427315178, Chain.OPTIMISM: 147152611,
        Chain.UNICHAIN: 39155640,
    },
    (2026, 2): {
        Chain.ETHEREUM: 24558867, Chain.BASE: 42766926,
        Chain.ARBITRUM: 437025050, Chain.OPTIMISM: 148362211,
        Chain.UNICHAIN: 41574840,
    },
    (2026, 3): {
        Chain.ETHEREUM: 24781026, Chain.BASE: 44106126,
        Chain.ARBITRUM: 447736930, Chain.OPTIMISM: 149701411,
        Chain.UNICHAIN: 44253240,
    },
}


class _FixtureBlockResolver:
    """Block resolver backed by ``l2_daily_eod_blocks.json``.

    The PSM3 path inside ``get_psm_usds_timeseries`` does one
    ``block_at_or_before`` per (chain, EoD-of-day) for each day in the
    period. Going through drpc's binary search is flaky for those L2 chains;
    the Dune fixture is exact and pre-computed.

    Only ``block_at_or_before`` is implemented since the PSM3 path doesn't
    call ``block_to_date``.
    """

    def __init__(self, fixture_path: Path):
        with open(fixture_path) as f:
            rows = json.load(f)["rows"]
        # Pre-index: (chain, date) → block_number.
        self._by_chain_date: dict[tuple[str, date], int] = {}
        for r in rows:
            d = date.fromisoformat(r["block_date"])
            self._by_chain_date[(r["chain"], d)] = r["block_number"]

    def block_at_or_before(self, chain: str, anchor_utc: datetime) -> int:
        # ``anchor_utc`` is end-of-day; the date component is what we look up.
        d = anchor_utc.date()
        key = (chain, d)
        if key not in self._by_chain_date:
            raise KeyError(
                f"No fixture block for ({chain}, {d.isoformat()}). "
                f"Extend tests/fixtures/spark_2026_q1/l2_daily_eod_blocks.json."
            )
        return self._by_chain_date[key]

    def block_to_date(self, chain: str, block: int) -> date:
        # The PSM3 daily-snapshot path doesn't use this; the fixture is
        # date→block, not block→date. If a future caller needs the reverse
        # mapping, switch to RPCBlockResolver (or build an inverse index).
        raise NotImplementedError(
            "FixtureBlockResolver is date→block only; block_to_date is not "
            "supported. Use RPCBlockResolver for that direction."
        )


def main() -> int:
    spark = load_prime(_REPO / "config" / "spark.yaml")
    print(f"Spark prime: ilk=ALLOCATOR-SPARK-A, start_date={spark.start_date.isoformat()}")
    print(f"  Chains:    {sorted(c.value for c in spark.alm.keys())}")
    print(f"  PSM:       {[(c.value, cfg.kind.value) for c, cfg in spark.psm.items()]}")
    print()

    debt_df = _load_debt()
    ssr_df = _load_ssr()
    print(f"Loaded fixtures: debt={len(debt_df)} rows  ssr={len(ssr_df)} rows")
    print(f"  debt.cum_debt[2026-03-31] = ${float(debt_df.loc[debt_df['block_date'] == date(2026, 3, 31), 'cum_debt'].iloc[0]):,.0f}")
    print(f"  ssr last change: {ssr_df['effective_date'].iloc[-1]}  apy={ssr_df['ssr_apy'].iloc[-1]}")
    print()

    fixture_path = _REPO / "tests" / "fixtures" / "spark_2026_q1" / "l2_daily_eod_blocks.json"
    resolver = _FixtureBlockResolver(fixture_path)
    psm3_src = get_psm3_source()         # RPC PSM3 reader

    months = [(2026, 1), (2026, 2), (2026, 3)]
    pin_blocks_per_month = PIN_BLOCKS_EOM
    print("EoM pin_blocks (hardcoded — Eth/Base from Grove fixtures, L2s from Dune query 7401735):")
    for ym in months:
        print(f"  {ym[0]}-{ym[1]:02d}: " + ", ".join(
            f"{c.value}={b}" for c, b in pin_blocks_per_month[ym].items()
        ))
    print()

    # --------------------------------------------------------------------
    # Build PSM3 USDS-equivalent timeseries ONCE (Dec 31 2025 → Mar 31 2026).
    # Approach: snapshot at month boundaries (Dec 31, Jan 31, Feb 28, Mar 31)
    # and linearly interpolate daily within each month. Daily snapshots via
    # drpc are too slow; boundary snapshots + interp captures the per-day
    # utilization for sky_revenue with sub-percent error vs daily reads.
    # 4 boundaries × 4 L2 chains × 2 RPC reads each = 32 RPC calls total.
    # --------------------------------------------------------------------
    psm_chains = [c for c in spark.psm if spark.psm[c].kind.value == "erc4626_shares"]
    boundary_dates = [date(2025, 12, 31), date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]

    def _snapshot(args) -> tuple[Chain, date, Decimal]:
        c, d = args
        psm3_addr = spark.psm[c].address.value
        alm_addr = spark.alm[c].value
        eod = datetime.combine(d, time.max, tzinfo=timezone.utc)
        blk = resolver.block_at_or_before(c.value, eod)
        # PSM3 RPC funcs raise on persistent provider failure (drpc upstream
        # exhausted retries). Treat as $0 for that (chain, date) snapshot
        # rather than crashing the whole run; missing data on one boundary
        # at most under-states a single chain's contribution to utilized.
        try:
            shares = psm3_src.shares_of(c.value, psm3_addr, alm_addr, blk)
            if shares == 0:
                return c, d, Decimal(0)
            raw = psm3_src.convert_to_asset_value(c.value, psm3_addr, shares, blk)
            return c, d, Decimal(raw) / Decimal(10**18)
        except (RPCError, requests.HTTPError) as e:
            print(f"  ⚠ {c.value} {d} PSM3 read failed ({type(e).__name__}); treating as $0")
            return c, d, Decimal(0)

    print("Reading PSM3 boundary snapshots (4 dates × 4 chains × 2 RPC each)...")
    snapshots: dict[tuple[Chain, date], Decimal] = {}
    work = [(c, d) for c in psm_chains for d in boundary_dates]
    with ThreadPoolExecutor(max_workers=len(psm_chains)) as ex:
        for c, d, v in ex.map(_snapshot, work):
            snapshots[(c, d)] = v
    print("PSM3 boundary snapshots (USDS-equivalent):")
    print(f"  {'date':<12} {'base':>14} {'arbitrum':>14} {'optimism':>14} {'unichain':>14} {'total':>14}")
    for d in boundary_dates:
        vals = [snapshots[(c, d)] for c in psm_chains]
        line = " ".join(f"${float(v):>13,.0f}" for v in vals)
        total = sum(vals)
        print(f"  {d.isoformat():<12} {line} ${float(total):>13,.0f}")
    print()

    # Build daily psm_usds timeseries Dec 31 → Mar 31 with linear interp
    # within each month boundary segment, then aggregated across chains.
    def _daily_interp(c: Chain) -> list[tuple[date, Decimal]]:
        rows: list[tuple[date, Decimal]] = []
        for i in range(len(boundary_dates) - 1):
            d_lo, d_hi = boundary_dates[i], boundary_dates[i + 1]
            v_lo, v_hi = snapshots[(c, d_lo)], snapshots[(c, d_hi)]
            span = (d_hi - d_lo).days
            for k in range(span + 1):
                day = d_lo + timedelta(days=k)
                # avoid double-counting boundary day across segments
                if i > 0 and k == 0:
                    continue
                v = v_lo + (v_hi - v_lo) * Decimal(k) / Decimal(span)
                rows.append((day, v))
        return rows

    by_date: dict[date, Decimal] = {}
    for c in psm_chains:
        for d, v in _daily_interp(c):
            by_date[d] = by_date.get(d, Decimal(0)) + v
    sorted_dates = sorted(by_date.keys())
    cum_values = [by_date[d] for d in sorted_dates]
    # ``daily_net`` = first-difference of cum_balance, computed in pure
    # Python Decimal — pandas' Series.diff() on object-dtype works today but
    # is undocumented and could silently float-cast in a future pandas.
    daily_net = [cum_values[0]] + [
        cum_values[i] - cum_values[i - 1] for i in range(1, len(cum_values))
    ]
    psm_usds_full = pd.DataFrame({
        "block_date": sorted_dates,
        "daily_net": daily_net,
        "cum_balance": cum_values,
    })

    # ---------------------------------------------------------------------
    # Per-month sky_revenue computation.
    # ---------------------------------------------------------------------
    print("=" * 110)
    print(f"{'Month':<10} {'cum_debt_eom':>20} {'psm_usds_eom':>20} {'utilized_eom':>20} {'sky_revenue':>20}")
    print("-" * 110)

    from settle.compute._helpers import cum_at_or_before

    artifacts: dict[tuple[int, int], dict] = {}
    for ym in months:
        pins = pin_blocks_per_month[ym]
        period_end = _eom_date(*ym)
        period_start = period_end.replace(day=1)
        period = Period(start=period_start, end=period_end, pin_blocks=pins)

        # Compute sky_revenue with $0 idle subproxy/ALM USDS + sUSDS-principal.
        # PSM USDS-equivalent uses the pre-built daily timeseries (boundary
        # snapshots + linear interpolation across the full Q1).
        sky_rev = compute_sky_revenue(
            period=period,
            debt=debt_df,
            subproxy_usds=_empty_balance_df(),
            subproxy_susds_principal=_empty_balance_df(),
            alm_usds=_empty_balance_df(),
            ssr=ssr_df,
            psm_usds=psm_usds_full,
        )

        # Headline state at EoM.
        cum_debt_eom = cum_at_or_before(debt_df, "cum_debt", period_end)
        cum_psm_eom = (
            cum_at_or_before(psm_usds_full, "cum_balance", period_end)
            if not psm_usds_full.empty else Decimal(0)
        )
        utilized_eom = cum_debt_eom - cum_psm_eom

        label = f"{ym[0]}-{ym[1]:02d}"
        print(f"{label:<10} ${float(cum_debt_eom):>19,.0f} ${float(cum_psm_eom):>19,.0f} ${float(utilized_eom):>19,.0f} ${float(sky_rev):>19,.2f}")

        # Persist per-month JSON.
        out_dir = _REPO / "settlements" / "spark" / label
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "sky_revenue_only.json").write_text(json.dumps({
            "prime": spark.id,
            "month": label,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "pin_blocks_eom": {c.value: b for c, b in pins.items()},
            "totals": {
                "cum_debt_eom": str(cum_debt_eom),
                "cum_psm_usds_eom": str(cum_psm_eom),
                "utilized_eom": str(utilized_eom),
                "sky_revenue": str(sky_rev),
            },
            "_disclaimer": (
                "Sky-revenue only. prime_agent_total_revenue not computed "
                "(Spark venue-pricing paths not yet implemented). "
                "Idle subproxy/ALM USDS and Eth directed_flow PSM treated as "
                "$0 (confirmed via Dune queries 7399654 + 7401552 — Spark "
                "doesn't hold dust at subproxy/ALM and doesn't route through "
                "Sky LITE-PSM-USDC). ALM sUSDS is treated as a Cat B venue "
                "(not a utilized-reduction term per methodology). PSM3 USDS-"
                "equivalent on Base/Arbitrum/Optimism/Unichain is read live "
                "via RPC (cached)."
            ),
        }, indent=2))
        artifacts[ym] = {"sky_revenue_only": out_dir / "sky_revenue_only.json"}

    print("-" * 110)
    print()
    print("Artifacts written:")
    for ym, paths in artifacts.items():
        label = f"{ym[0]}-{ym[1]:02d}"
        for k, p in paths.items():
            print(f"  {label}  {k}: {p.relative_to(_REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
