"""Shared Grove-fixture loader.

Both ``scripts/run_grove_2026_03.py`` (single-month acceptance) and
``scripts/run_grove_2026_q1.py`` (multi-month Q1 2026) build their
``Sources`` from the same captured-fixture set:

* ``tests/fixtures/grove_2026_03/dune_outputs.json`` — debt, balances, SSR,
  per-venue mint/burn + cum_balance, V3 events, NAV overrides.
* ``tests/fixtures/grove_2026_03/blocks_at_eod*.json`` — block-resolver
  rows per chain (covers the full prime lifetime).

This module factors out the ~150 lines of fixture-loading + source-routing
that the two scripts otherwise duplicate. Build a ``Sources`` once per run,
or for multi-month: rebuild per month (cheap — pure-memory) so each call
gets a fresh ``MockBalanceSource.cumulative_calls`` etc.

Live RPC primitives (position_balance, convert_to_assets, NAV oracles,
V3 NFT enumeration, Curve pool reads) come from the registry defaults —
they require ``ETH_RPC`` / ``BASE_RPC`` / ``AVALANCHE_C_RPC`` / ``PLUME_RPC``
env vars.
"""

from __future__ import annotations

import json
from decimal import Decimal as _D
from pathlib import Path
from typing import Any

import pandas as pd

from settle.compute import Sources
from settle.domain import Chain
from settle.domain.config import load_prime
from tests.fixtures.mock_sources import (
    MockBalanceSource,
    MockDebtSource,
    MockSSRSource,
)

USDS = bytes.fromhex("dc035d45d973e3ec169d2276ddab16f1e407384f")
SUSDS = bytes.fromhex("a3931d71877c0e7a3148cb7eb4463524fec27fbd")

_DECIMAL_COLS = (
    "daily_inflow", "cum_inflow",
    "daily_net", "cum_balance",
    "daily_dart", "cum_debt",
    "signed_amount",
    "ssr_apy",
)


def df_with_dates(rows: list[dict], date_col: str) -> pd.DataFrame:
    """Build a DataFrame from JSON-loaded fixture rows with type coercion.

    JSON deserializes numeric values as ``float``; downstream consumers expect
    ``Decimal`` (matching the live Dune source path which applies
    ``_to_decimal(str(v))``). Coerce known numeric columns so
    ``Decimal(str(some_float))`` later in the pipeline doesn't lose precision.
    """
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df[date_col] = pd.to_datetime(df[date_col]).dt.date
    for c in _DECIMAL_COLS:
        if c in df.columns:
            df[c] = df[c].apply(lambda v: _D(str(v)))
    return df


def load_grove_and_fixtures(repo: Path, fixture_dir: str = "grove_2026_03"):
    """Load the ``grove`` Prime + all block-resolver fixtures + the
    Dune-output bundle. ``fixture_dir`` selects the snapshot generation
    (``grove_2026_03`` for Q1 acceptance, ``grove_2026_04`` for April,
    etc.). Returns ``(grove, dune_outputs, blocks_by_chain)``.
    """
    grove = load_prime(repo / "config" / "grove.yaml")
    base = repo / "tests/fixtures" / fixture_dir
    fixtures = json.loads((base / "dune_outputs.json").read_text())
    blocks_by_chain = {
        "ethereum":    json.loads((base / "blocks_at_eod.json").read_text()),
        "base":        json.loads((base / "blocks_at_eod_base.json").read_text()),
        "avalanche_c": json.loads((base / "blocks_at_eod_avalanche.json").read_text()),
        "plume":       json.loads((base / "blocks_at_eod_plume.json").read_text()),
    }
    # Monad block resolver is optional — only the April fixture captures it.
    monad_path = base / "blocks_at_eod_monad.json"
    if monad_path.exists():
        blocks_by_chain["monad"] = json.loads(monad_path.read_text())
    return grove, fixtures, blocks_by_chain


def build_grove_sources(grove, fixtures: dict, blocks_by_chain: dict[str, Any]) -> Sources:
    """Assemble the Grove ``Sources`` bundle from captured fixtures.

    Builds a routed ``MockBalanceSource`` that dispatches per ``(token, holder)``
    or ``(token, from_addr, to_addr)`` based on the captured fixture keys, plus
    a Multi-chain ``DuneBlockResolver`` and a V3 source whose
    ``liquidity_events_in_pool`` returns the pre-fetched events filtered to the
    requested block range.

    NAV oracle overrides (for blocks where Chronicle hadn't yet started writing,
    e.g. STAC at Dec 31 2025) are wired via ``Sources.nav_oracle_resolver`` so
    the override is scoped to this Sources instance — no module-level mutation
    of the global registry.
    """
    debt_df    = df_with_dates(fixtures["debt"]["rows"], "block_date")
    sub_usds   = df_with_dates(fixtures["subproxy_usds"]["rows"], "block_date")
    sub_susds  = df_with_dates(fixtures["subproxy_susds"]["rows"], "block_date")
    alm_usds   = df_with_dates(fixtures["alm_usds"]["rows"], "block_date")
    ssr_df     = df_with_dates(fixtures["ssr"]["rows"], "effective_date")

    grove_sub = grove.subproxy[Chain.ETHEREUM].value
    grove_alm = grove.alm[Chain.ETHEREUM].value
    all_alm_values = {grove.alm[c].value for c in grove.chains}
    ZERO = b"\x00" * 20

    # Build per-venue fixture lookups keyed by token addresses.
    directed_inflow_fixtures: dict[tuple[bytes, bytes, bytes], pd.DataFrame] = {}
    for v in grove.venues:
        cat = v.pricing_category.value
        vid = v.id.lower()
        token = v.token.address.value
        alm = grove.alm[v.chain].value
        if cat == "C":
            directed_inflow_fixtures[(token, ZERO, alm)] = df_with_dates(
                fixtures[f"atoken_{vid}_mints"]["rows"], "block_date",
            )
            directed_inflow_fixtures[(token, alm, ZERO)] = df_with_dates(
                fixtures[f"atoken_{vid}_burns"]["rows"], "block_date",
            )
        elif cat == "B" and vid in ("e4", "e5", "e6", "e19", "e23"):
            directed_inflow_fixtures[(token, ZERO, alm)] = df_with_dates(
                fixtures[f"vault_{vid}_mints"]["rows"], "block_date",
            )
            directed_inflow_fixtures[(token, alm, ZERO)] = df_with_dates(
                fixtures[f"vault_{vid}_burns"]["rows"], "block_date",
            )
        # Cash-distribution sources (e.g. Galaxy CLO USDC sweeps for E21).
        # ``cash_distributions`` queries ``directed_inflow_timeseries`` keyed by
        # (token, payer, ALM-on-payer-chain). The fixture key is
        # ``cash_dist_{vid}`` and may carry distributions on a DIFFERENT chain
        # from the venue's home chain (Galaxy E21 lives on Avalanche but
        # distributes USDC to the Ethereum ALM).
        for src in v.cash_distributions:
            chain = src.chain if src.chain is not None else v.chain
            if chain not in grove.alm:
                continue
            fx_key = f"cash_dist_{vid}"
            if fx_key not in fixtures:
                continue
            directed_inflow_fixtures[(
                src.token.value, src.payer.value, grove.alm[chain].value,
            )] = df_with_dates(fixtures[fx_key]["rows"], "block_date")

        # display_only venue principal-out caps. The compute layer in
        # monthly_pnl looks up ``directed_inflow_timeseries(token, ALM,
        # holder_override)`` for each display-only venue to build the
        # paired_principal_cap series. Without the fixture, the cap is $0
        # and inflows from ``paired_source`` to the anchor get fully
        # reclassified as realized revenue (E14 April $6.5M phantom).
        #
        # Filter is (display_only AND holder_override AND has paired_with) —
        # not restricted to pricing_category="EOA" because the compute-side
        # wiring at monthly_pnl.py:~2204 iterates ALL display_only venues
        # regardless of category. A display_only venue with no paired_with
        # doesn't drive a cap and doesn't need this fixture.
        if (v.display_only and v.holder_override is not None
                and getattr(v, "paired_with", None) is not None):
            fx_key = f"eoa_outflow_{vid}"
            if fx_key in fixtures:
                alm = grove.alm[v.chain].value
                directed_inflow_fixtures[(
                    v.token.address.value, alm, v.holder_override.value,
                )] = df_with_dates(fixtures[fx_key]["rows"], "block_date")
            else:
                # Silent fallback to empty cap reproduces the original phantom
                # revenue at the anchor venue. Warn at load time so the
                # operator notices BEFORE inspecting suspect output numbers.
                import warnings as _warnings
                _warnings.warn(
                    f"grove_fixture_loader: display_only venue {v.id!r} on "
                    f"chain {v.chain.value!r} has no {fx_key!r} fixture — "
                    f"paired_principal_cap for its anchor will be $0, and "
                    f"inflows from its paired_source will be misclassified "
                    f"as realized revenue. Capture eoa_outflow_{vid} via "
                    f"_capture_dune_fixtures.py to fix.",
                    stacklevel=2,
                )

    cum_balance_fixtures: dict[bytes, pd.DataFrame] = {}
    for v in grove.venues:
        if v.pricing_category.value not in ("A", "E"):
            continue
        key = f"cum_balance_{v.id.lower()}"
        if key in fixtures:
            cum_balance_fixtures[v.token.address.value] = df_with_dates(
                fixtures[key]["rows"], "block_date",
            )

    # Per-(token, holder) inflow fixtures. Keying by ``token`` alone would
    # silently spill one venue's flow data into a different venue that
    # happens to share the token (e.g. E15 USDC at the main ALM and E32 USDC
    # at an alt-holder both use the USDC token address). Cat A venues whose
    # holder doesn't match a captured fixture get an empty frame — same
    # behaviour as a venue that isn't in the fixture at all.
    inflow_by_cp_fixtures: dict[tuple[bytes, bytes], pd.DataFrame] = {}
    for v in grove.venues:
        if v.pricing_category.value != "A":
            continue
        key = f"inflow_by_counterparty_{v.id.lower()}"
        if key in fixtures:
            df = df_with_dates(fixtures[key]["rows"], "block_date")
            if not df.empty:
                df["counterparty"] = df["counterparty"].apply(
                    lambda h: bytes.fromhex(h.removeprefix("0x")).rjust(20, b"\x00")
                )
            # Holder the fixture was captured against. Prefer the explicit
            # ``_holder`` metadata field; fall back to the venue's effective
            # holder (holder_override or main ALM) for older fixtures that
            # didn't record it.
            fx_holder_str = fixtures[key].get("_holder")
            if fx_holder_str:
                fx_holder = bytes.fromhex(
                    fx_holder_str.removeprefix("0x")
                ).rjust(20, b"\x00")
            else:
                fx_holder = (v.holder_override or grove.alm[v.chain]).value
            inflow_by_cp_fixtures[(v.token.address.value, fx_holder)] = df

    class _RoutedBalances(MockBalanceSource):
        def cumulative_balance_timeseries(
            self, chain, token, holder, start, pin_block, min_transfer_amount=None,
        ):
            self.cumulative_calls.append((chain, token, holder, start, pin_block, min_transfer_amount))
            if token == USDS  and holder == grove_sub:  return sub_usds
            if token == SUSDS and holder == grove_sub:  return sub_susds
            if token == USDS  and holder == grove_alm:  return alm_usds
            if holder in all_alm_values and token in cum_balance_fixtures:
                return cum_balance_fixtures[token]
            return df_with_dates([], "block_date")

        def directed_inflow_timeseries(self, chain, token, from_addr, to_addr, start, pin_block):
            df = directed_inflow_fixtures.get((token, from_addr, to_addr))
            if df is not None:
                return df
            return pd.DataFrame({"block_date": [], "daily_inflow": [], "cum_inflow": []})

        def inflow_by_counterparty(self, chain, token, holder, start, pin_block):
            df = inflow_by_cp_fixtures.get((token, holder))
            if df is not None:
                return df
            return pd.DataFrame({"block_date": [], "counterparty": [], "signed_amount": []})

    # V3 events fixtures — decode once, dispatch per call by (owner, pool)
    # so multiple V3 fixtures (e.g. E12 at the main ALM and E30 at the alt-
    # holder, both in the same AUSD/USDC pool) coexist without spilling
    # into each other. Each fixture is scoped to the (owner, pool) it was
    # captured for; callers requesting an un-captured (owner, pool) get an
    # empty event list — same behaviour as a venue that isn't in the
    # fixture at all.
    from settle.extract.uniswap_v3 import _decode_liquidity_log
    from settle.normalize.sources.uniswap_v3 import RPCUniswapV3PositionSource

    def _addr_to_bytes(s: str) -> bytes:
        return bytes.fromhex(s.removeprefix("0x")).rjust(20, b"\x00")

    v3_events_by_scope: dict[tuple[bytes, bytes], list] = {}
    for key in fixtures:
        if not key.startswith("v3_liquidity_events_"):
            continue
        entry = fixtures[key]
        events = [
            _decode_liquidity_log({
                "blockNumber": hex(int(r["block_number"])),
                "transactionHash": r["tx_hash"],
                "logIndex": hex(int(r["log_index"])),
                "topics": [r["topic0"], r["topic1"]],
                "data": r["data"],
            })
            for r in entry["rows"]
        ]
        # ``_holder`` / ``_pool`` metadata identify the scope this fixture
        # was captured against. Fall back to (main ALM, ?) when missing for
        # backward-compatibility with the original e12 fixture format.
        owner_b = _addr_to_bytes(entry["_holder"]) if "_holder" in entry else grove_alm
        pool_b = _addr_to_bytes(entry["_pool"]) if "_pool" in entry else _addr_to_bytes(
            "0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d"
        )
        v3_events_by_scope[(owner_b, pool_b)] = events

    class _V3Mixed(RPCUniswapV3PositionSource):
        def liquidity_events_in_pool(self, chain, owner, pool, from_block, to_block):
            events = v3_events_by_scope.get((owner, pool))
            if events is None:
                return []
            return [e for e in events if from_block < e.block_number <= to_block]

    from settle.normalize.sources.dune_block_resolver import (
        DuneBlockResolver, MultiChainBlockResolver,
    )
    resolver = MultiChainBlockResolver({
        chain: DuneBlockResolver(chain=chain, prefetched_rows=blocks["rows"])
        for chain, blocks in blocks_by_chain.items()
    })

    # NAV oracle overrides (e.g. STAC at Dec 31 2025 where Chronicle hadn't
    # started writing yet). Built as a Sources-scoped resolver so the override
    # doesn't leak into other tests / scripts running in the same process.
    nav_overrides: dict[tuple[bytes, int], _D] = {}
    for r in fixtures.get("nav_overrides", {}).get("rows", []):
        ora = bytes.fromhex(r["oracle"].removeprefix("0x")).rjust(20, b"\x00")
        nav_overrides[(ora, int(r["block"]))] = _D(r["nav"])

    nav_resolver = None
    if nav_overrides:
        from settle.normalize.sources.oracles import ChronicleNavSource
        from settle.normalize.registry import get_nav_oracle_source as _default_resolve

        class _NavWithOverride(ChronicleNavSource):
            def nav_at(self, chain, oracle_address, block):
                key = (oracle_address, block)
                if key in nav_overrides:
                    return nav_overrides[key]
                return super().nav_at(chain, oracle_address, block)

        def nav_resolver(kind: str):  # noqa: F811 — closure assignment
            if kind == "chronicle":
                return _NavWithOverride()
            return _default_resolve(kind)

    return Sources(
        debt=MockDebtSource(debt_df),
        balance=_RoutedBalances(),
        ssr=MockSSRSource(ssr_df),
        v3_position=_V3Mixed(),
        block_resolver=resolver,
        nav_oracle_resolver=nav_resolver,
    )
