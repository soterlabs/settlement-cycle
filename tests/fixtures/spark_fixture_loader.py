"""Shared Spark-fixture loader (mirrors ``grove_fixture_loader.py``).

The runner (`scripts/run_spark_2026_q1.py`) builds its ``Sources`` from
captured-fixture sets:

* ``debt_timeseries.json``           — ALLOCATOR-SPARK-A debt
* ``cat_b_cum_balance.json``         — Cat B per-venue cum_balance at the
                                       per-chain Spark ALM (Eth + L2s)
* ``cat_e_cum_balance.json``         — Cat E (RWA) per-venue cum_balance
                                       (BUIDL filtered via min_transfer)
* ``l2_daily_eod_blocks.json``       — Base/Arb/Op/Uni daily blocks
* ``eth_avalanche_daily_eod_blocks.json`` — Eth + Avalanche-C blocks

SSR is reused from Grove's fixture (Sky-wide, identical across primes).

Live RPC primitives (position_balance, convert_to_assets, NAV oracles,
PSM3, Curve pool reads) come from the registry defaults — they require the
chain RPC env vars in ``.env``.
"""

from __future__ import annotations

import json
from decimal import Decimal as _D
from pathlib import Path

import pandas as pd

from settle.compute import Sources
from settle.domain import Chain
from settle.domain.config import load_prime
from tests.fixtures.mock_sources import (
    MockBalanceSource,
    MockDebtSource,
    MockSSRSource,
)

USDS_ETH = bytes.fromhex("dc035d45d973e3ec169d2276ddab16f1e407384f")
SUSDS_ETH = bytes.fromhex("a3931d71877c0e7a3148cb7eb4463524fec27fbd")

_DECIMAL_COLS = (
    "daily_inflow", "cum_inflow",
    "daily_net", "cum_balance",
    "daily_dart", "cum_debt",
    "ssr_apy",
)


def _df_with_dates(rows: list[dict], date_col: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df[date_col] = pd.to_datetime(df[date_col]).dt.date
    for c in _DECIMAL_COLS:
        if c in df.columns:
            df[c] = df[c].apply(lambda v: _D(str(v)))
    return df


def _empty_balance_df() -> pd.DataFrame:
    return pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})


def _empty_directed_df() -> pd.DataFrame:
    return pd.DataFrame({"block_date": [], "daily_inflow": [], "cum_inflow": []})


def load_spark_and_fixtures(repo: Path):
    """Load the ``spark`` Prime + fixture bundles. Returns ``(spark, fixtures)``."""
    spark = load_prime(repo / "config" / "spark.yaml")
    fdir = repo / "tests" / "fixtures" / "spark_2026_q1"
    fixtures = {
        "debt": json.loads((fdir / "debt_timeseries.json").read_text()),
        "cat_b": json.loads((fdir / "cat_b_cum_balance.json").read_text()),
        "cat_e": json.loads((fdir / "cat_e_cum_balance.json").read_text()),
        "blocks_l2": json.loads((fdir / "l2_daily_eod_blocks.json").read_text()),
        "blocks_eth_ava": json.loads((fdir / "eth_avalanche_daily_eod_blocks.json").read_text()),
        # SSR comes from Grove's fixture (Sky-wide).
        "ssr": json.loads(
            (repo / "tests/fixtures/grove_2026_03/dune_outputs.json").read_text()
        )["ssr"],
    }
    return spark, fixtures


def build_spark_sources(
    spark, fixtures: dict, *,
    pin_blocks_som=None, pin_blocks_eom=None,
    period_start=None, period_end=None,
) -> Sources:
    """Assemble the Spark ``Sources`` bundle from captured fixtures.

    Routing strategy (per-venue):
    * Cat A: empty cum_balance + empty inflow (revenue=0 with empty
      ``external_alm_sources``); SoM/EoM value snapshots come from RPC
      ``balanceOf`` via ``position_balance``.
    * Cat B: per-(token, holder=ALM) cum_balance from ``cat_b_cum_balance.json``.
    * Cat C: no fixture — pure RPC ``balanceOf`` + ``scaledBalanceOf``.
    * Cat E: per-(token, holder=Eth ALM) cum_balance from ``cat_e_cum_balance.json``.
    * Cat F: no fixture — pure RPC Curve pool reads.
    """
    debt_df = _df_with_dates(fixtures["debt"]["rows"], "block_date")
    ssr_df = _df_with_dates(fixtures["ssr"]["rows"], "effective_date")

    # Cat B routing.
    # _shares_to_usd_inflow_timeseries calls directed_inflow_timeseries with
    # (from=0x0, to=ALM) for mints and (from=ALM, to=0x0) for burns. We don't
    # have separately captured mint/burn rows — only the net daily flow per
    # venue (daily_net). Synthesize mint/burn dataframes by splitting daily_net
    # on sign: positive → mint row (daily_inflow=daily_net), negative → burn
    # row (daily_inflow=-daily_net). The net daily share-flow consumed by
    # `_shares_to_usd_inflow_timeseries` (mint − burn) is preserved exactly;
    # the gross mint vs gross burn split is approximate (a day with both a
    # mint and a burn would have its gross flows merged into a single net),
    # but the inflow USD pricing is `net_share_flow × pps`, so the
    # approximation is exact for our purpose.
    # Cat B mint/burn rows must be filtered to dates the block_resolver
    # fixture covers (2025-12-31 → 2026-03-31). _shares_to_usd_inflow_timeseries
    # iterates EVERY mint/burn date (not just period dates) to build the inflow
    # timeseries; pre-period rows would cause block_resolver fallback to RPC,
    # which dies on cross-chain binary search starting at block 0. Pre-period
    # flows don't affect period revenue (the period's value_som already absorbs
    # them via RPC balance_at), so dropping them is safe.
    from datetime import date as _date, timedelta as _td
    # SoM anchor for the synthetic Cat B mint/burn frames: the day before
    # period.start. _shares_to_usd_inflow_timeseries iterates EVERY mint/burn
    # date so pre-period rows would force block_resolver fallback to RPC
    # (broken for some L2s and unnecessary). Pre-period flows don't affect
    # period revenue (RPC balance_at at SoM block already absorbs them).
    period_filter_start = (period_start - _td(days=1)) if period_start else _date(2025, 12, 31)
    ZERO = b"\x00" * 20
    cat_b_directed: dict[tuple[bytes, bytes, bytes], pd.DataFrame] = {}
    cat_b_cum_by_token_holder: dict[tuple[bytes, bytes], pd.DataFrame] = {}
    cat_b_rows = fixtures["cat_b"]["rows"]
    for v in spark.venues:
        if v.pricing_category.value != "B":
            continue
        alm = spark.alm[v.chain].value
        rows_for_v = [r for r in cat_b_rows if r["venue_id"] == v.id]
        df = _df_with_dates(rows_for_v, "block_date")
        if df.empty:
            cat_b_directed[(v.token.address.value, ZERO, alm)] = _empty_directed_df()
            cat_b_directed[(v.token.address.value, alm, ZERO)] = _empty_directed_df()
            cat_b_cum_by_token_holder[(v.token.address.value, alm)] = df
            continue
        df = df.sort_values("block_date").reset_index(drop=True)
        cat_b_cum_by_token_holder[(v.token.address.value, alm)] = (
            df[["block_date", "daily_net", "cum_balance"]].copy()
        )
        # Split into synthetic mint/burn frames, keeping only Q1-period rows.
        mint_rows = []
        burn_rows = []
        cum_mint = _D(0)
        cum_burn = _D(0)
        for _, r in df.iterrows():
            if r["block_date"] < period_filter_start:
                continue
            net = r["daily_net"]
            if net > 0:
                cum_mint += net
                mint_rows.append({
                    "block_date": r["block_date"],
                    "daily_inflow": net, "cum_inflow": cum_mint,
                })
            elif net < 0:
                cum_burn += -net
                burn_rows.append({
                    "block_date": r["block_date"],
                    "daily_inflow": -net, "cum_inflow": cum_burn,
                })
        cat_b_directed[(v.token.address.value, ZERO, alm)] = (
            _df_with_dates(mint_rows, "block_date") if mint_rows else _empty_directed_df()
        )
        cat_b_directed[(v.token.address.value, alm, ZERO)] = (
            _df_with_dates(burn_rows, "block_date") if burn_rows else _empty_directed_df()
        )

    # Cat A routing: synthesize a 2-row cumulative_balance_timeseries from
    # RPC ``balanceOf`` at SoM/EoM. The compute-layer Cat A fallback (when
    # external_alm_sources is empty AND inflow_by_counterparty returns empty)
    # consumes this to set capital_net = Δvalue, producing revenue = 0 — the
    # correct behavior for par-stables held at the ALM with no off-chain
    # yield source. Spark has no external_alm_sources, so all Cat A balance
    # changes are value-preserving (PSM swap, allocator buffer, etc.).
    # Coverage assertion: for any Cat B venue with a non-zero EoM cum_balance
    # in the fixture, demand at least one row dated >= period_filter_start.
    # Otherwise the synthesized mint/burn frames are empty for that venue,
    # period_inflow defaults to 0, and revenue silently equals Δvalue (often
    # a phantom yield). Fixtures with only pre-period anchor rows must be
    # extended OR confirmed to have $0 mid-period activity before the run.
    if period_start is not None:
        for v in spark.venues:
            if v.pricing_category.value != "B":
                continue
            alm = spark.alm[v.chain].value
            cum_df = cat_b_cum_by_token_holder.get((v.token.address.value, alm))
            if cum_df is None or cum_df.empty:
                continue
            cum_eom = cum_df["cum_balance"].iloc[-1]
            # Materiality threshold: $10K. Below this, missing in-period rows
            # produce at most ~$10K of phantom revenue (bounded by the EoM
            # value), which is below the precision we report at.
            if abs(cum_eom) < _D(10_000):
                continue
            in_period = cum_df[cum_df["block_date"] >= period_filter_start]
            if in_period.empty:
                # No mid-period rows but the venue has a balance — flag.
                # Acceptable if the team has confirmed no Q1 flows; in that
                # case set SETTLE_SPARK_ALLOW_PRE_PERIOD_ANCHOR=1 to bypass.
                import os as _os
                if _os.environ.get("SETTLE_SPARK_ALLOW_PRE_PERIOD_ANCHOR") != "1":
                    raise ValueError(
                        f"Spark Cat B venue {v.id} ({v.token.symbol}, {v.chain.value}) "
                        f"has cum_balance ≈ ${cum_eom:,.0f} at fixture EoM but no "
                        f"rows >= {period_filter_start} in cat_b_cum_balance.json. "
                        f"period_inflow would default to 0 → revenue = Δvalue (phantom yield). "
                        "Verify with the Dune source that no mid-period flows occurred "
                        "and set SETTLE_SPARK_ALLOW_PRE_PERIOD_ANCHOR=1 to bypass, or "
                        "extend the fixture."
                    )

    cat_a_cum_by_token_holder: dict[tuple[bytes, bytes], pd.DataFrame] = {}
    if pin_blocks_som and pin_blocks_eom:
        if period_start is None or period_end is None:
            raise ValueError(
                "build_spark_sources(pin_blocks_som=…, pin_blocks_eom=…) also "
                "requires period_start and period_end (the calendar dates of "
                "the month being settled). Refusing to silently skip Cat A "
                "synthesis based on hard-coded block heuristics — that produced "
                "phantom revenue in earlier versions."
            )
        from settle.extract import rpc as _rpc
        from settle.domain.primes import Address as _Addr, Chain as _Chain

        # SoM anchor date is the prior-month-end (period.start - 1) so
        # cum_at_or_before returns the SoM balance for any day in
        # [period.start, period.end - 1]. EoM anchor date = period_end.
        som_date = period_start - _td(days=1)
        eom_date = period_end

        def _balance_decimal(chain, token, holder, block, decimals):
            raw = _rpc.balance_of(_Chain(chain), _Addr(token), _Addr(holder), block)
            return _D(raw) / _D(10 ** decimals)

        for v in spark.venues:
            if v.pricing_category.value != "A":
                continue
            if v.chain not in pin_blocks_som or v.chain not in pin_blocks_eom:
                continue
            som_blk = pin_blocks_som[v.chain]
            eom_blk = pin_blocks_eom[v.chain]
            bal_som = _balance_decimal(
                v.chain.value, v.token.address.value,
                spark.alm[v.chain].value, som_blk, v.token.decimals,
            )
            bal_eom = _balance_decimal(
                v.chain.value, v.token.address.value,
                spark.alm[v.chain].value, eom_blk, v.token.decimals,
            )
            rows = [
                {"block_date": som_date, "daily_net": bal_som, "cum_balance": bal_som},
                {"block_date": eom_date, "daily_net": bal_eom - bal_som, "cum_balance": bal_eom},
            ]
            df = pd.DataFrame(rows)
            cat_a_cum_by_token_holder[(v.token.address.value, spark.alm[v.chain].value)] = df

    # Cat E routing: only Eth ALM holders.
    cat_e_by_token: dict[bytes, pd.DataFrame] = {}
    cat_e_rows = fixtures["cat_e"]["rows"]
    eth_alm = spark.alm[Chain.ETHEREUM].value
    for v in spark.venues:
        if v.pricing_category.value != "E":
            continue
        rows_for_v = [r for r in cat_e_rows if r["venue_id"] == v.id]
        df = _df_with_dates(rows_for_v, "block_date")
        if not df.empty:
            df = df[["block_date", "daily_net", "cum_balance"]].sort_values("block_date").reset_index(drop=True)
        cat_e_by_token[v.token.address.value] = df

    class _RoutedBalances(MockBalanceSource):
        def cumulative_balance_timeseries(
            self, chain, token, holder, start, pin_block, min_transfer_amount=None,
        ):
            self.cumulative_calls.append(
                (chain, token, holder, start, pin_block, min_transfer_amount)
            )
            # Cat B cum_balance (used by some pre-checks, e.g. zero-balance
            # short-circuit in get_position_value).
            df = cat_b_cum_by_token_holder.get((token, holder))
            if df is not None:
                return df
            # Cat E routing (Eth ALM)
            if holder == eth_alm and token in cat_e_by_token:
                return cat_e_by_token[token]
            # Cat A routing: synthesized SoM/EoM 2-row frame so the compute-
            # layer Cat A fallback (when both inflow_by_counterparty and
            # external_alm_sources are empty) treats balance changes as
            # value-preserving capital → revenue = 0.
            df = cat_a_cum_by_token_holder.get((token, holder))
            if df is not None:
                return df
            # Spark Eth subproxy/ALM raw USDS+sUSDS confirmed ~$0 (dust).
            return _empty_balance_df()

        def directed_inflow_timeseries(self, chain, token, from_addr, to_addr, start, pin_block):
            # Cat B mint/burn (synthesized from daily_net split on sign).
            df = cat_b_directed.get((token, from_addr, to_addr))
            if df is not None:
                return df
            # Spark's Eth directed_flow PSM = $0 (Dune query 7401552 verified
            # zero rows). All other directed paths default to empty.
            return _empty_directed_df()

        def inflow_by_counterparty(self, chain, token, holder, start, pin_block):
            # Spark.external_alm_sources is empty → Cat A revenue = 0.
            return pd.DataFrame({
                "block_date": [], "counterparty": [], "signed_amount": [],
            })

    # Block resolver: combine L2 + Eth + Avalanche-C fixtures into a single
    # multi-chain MockBlockResolver, with the Spark fixture file's exact
    # date→block mapping.
    blocks_by_chain_date: dict[tuple[str, "date"], int] = {}
    from datetime import date as _date
    for r in fixtures["blocks_l2"]["rows"]:
        d = _date.fromisoformat(r["block_date"])
        blocks_by_chain_date[(r["chain"], d)] = r["block_number"]
    for r in fixtures["blocks_eth_ava"]["rows"]:
        d = _date.fromisoformat(r["block_date"])
        blocks_by_chain_date[(r["chain"], d)] = r["block_number"]

    class _FixtureMultiResolver:
        def block_at_or_before(self, chain: str, anchor_utc):
            d = anchor_utc.date()
            key = (chain, d)
            if key not in blocks_by_chain_date:
                # Fall back to RPC for chains/dates not in the fixture
                # (e.g. avalanche_c daily-EoD requests outside the SoM/EoM dates
                # we captured).
                from settle.normalize.registry import get_block_resolver
                return get_block_resolver().block_at_or_before(chain, anchor_utc)
            return blocks_by_chain_date[key]

        def block_to_date(self, chain: str, block: int):
            from settle.normalize.registry import get_block_resolver
            return get_block_resolver().block_to_date(chain, block)

    return Sources(
        debt=MockDebtSource(debt_df),
        balance=_RoutedBalances(),
        ssr=MockSSRSource(ssr_df),
        block_resolver=_FixtureMultiResolver(),
        # position_balance, convert_to_assets, psm3, curve_pool, v3_position
        # default to RPC via the registry.
    )
