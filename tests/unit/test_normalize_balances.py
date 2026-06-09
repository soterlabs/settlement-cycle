"""Unit tests for `settle.normalize.balances` using mock sources."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from settle.domain import Address, Chain, Month, Period
from settle.domain.config import load_prime
from settle.normalize.balances import (
    get_alm_balance_timeseries,
    get_subproxy_balance_timeseries,
    get_venue_inflow_timeseries,
)

from ..fixtures.mock_sources import MockBalanceSource


def _obex(config_dir: Path):
    return load_prime(config_dir / "obex.yaml")


def _period() -> Period:
    return Period.from_month(Month(2026, 4), pin_blocks={Chain.ETHEREUM: 24971074})


def test_subproxy_balance_uses_prime_subproxy_address(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]                    # syrupUSDC
    underlying = venue.underlying             # USDC
    assert underlying is not None

    src = MockBalanceSource(
        cumulative_df=pd.DataFrame({
            "block_date": [date(2025, 11, 17)],
            "daily_net": [21_000_000.0],
            "cum_balance": [21_000_000.0],
        }),
    )

    df = get_subproxy_balance_timeseries(obex, Chain.ETHEREUM, underlying, _period(), source=src)
    assert df.iloc[0].cum_balance == 21_000_000.0

    chain, token, holder, start, pin, _min_transfer = src.cumulative_calls[0]
    assert chain == "ethereum"
    assert holder == obex.subproxy[Chain.ETHEREUM].value
    assert token == underlying.address.value
    assert start == obex.start_date
    assert pin == 24971074


def test_alm_balance_uses_prime_alm_address(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]
    src = MockBalanceSource()

    get_alm_balance_timeseries(obex, Chain.ETHEREUM, venue.token, _period(), source=src)

    chain, token, holder, _, _, _ = src.cumulative_calls[0]
    assert holder == obex.alm[Chain.ETHEREUM].value
    assert token == venue.token.address.value


def test_subproxy_balance_rejects_chain_not_configured(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]
    underlying = venue.underlying
    assert underlying is not None

    period = Period.from_month(Month(2026, 4), pin_blocks={Chain.BASE: 999})
    src = MockBalanceSource()
    with pytest.raises(ValueError, match="no subproxy on base"):
        get_subproxy_balance_timeseries(obex, Chain.BASE, underlying, period, source=src)


def test_venue_inflow_directs_alm_to_venue(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]                    # syrupUSDC
    underlying = venue.underlying             # USDC
    assert underlying is not None

    src = MockBalanceSource(
        directed_df=pd.DataFrame({
            "block_date": [date(2025, 11, 18)],
            "daily_inflow": [50_000_000.0],
            "cum_inflow": [50_000_000.0],
        }),
    )

    df = get_venue_inflow_timeseries(
        obex, Chain.ETHEREUM, underlying, venue.token.address, _period(), source=src,
    )
    assert df.iloc[0].cum_inflow == 50_000_000.0

    chain, token, from_addr, to_addr, start, pin = src.directed_calls[0]
    assert chain == "ethereum"
    assert from_addr == obex.alm[Chain.ETHEREUM].value
    assert to_addr == venue.token.address.value
    assert token == underlying.address.value
    assert start == obex.start_date
    assert pin == 24971074


def test_period_pin_block_required_for_chain(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]
    underlying = venue.underlying
    assert underlying is not None

    # Period has no pin_block at all
    period = Period.from_month(Month(2026, 4), pin_blocks={})
    src = MockBalanceSource()
    with pytest.raises(ValueError, match="missing pin_block for chain ethereum"):
        get_subproxy_balance_timeseries(obex, Chain.ETHEREUM, underlying, period, source=src)


# ----------------------------------------------------------------------------
# On-chain anchor (som_block + balance_at) — fix for Spark SubProxy holding
# ~$30–37M USDS funded via pre-period Sky allocations Dune tokens.transfers
# doesn't surface for the SubProxy address.
# ----------------------------------------------------------------------------

def test_subproxy_balance_anchors_to_on_chain_when_seed_present(config_dir: Path, caplog):
    """``balance_at(som_block)`` exceeds Dune-tracked cum_balance → seed row
    is prepended at ``prime.start_date`` AND every existing row is shifted by
    the seed. EoM cross-check confirms consistency post-shift."""
    import logging
    obex = _obex(config_dir)
    venue = obex.venues[0]
    underlying = venue.underlying
    assert underlying is not None

    # Dune fixture: subproxy events show $5M (Feb 1) then $6M (Mar 15).
    # All rows pre-date period.start (April 1) → tracked_som = $6M.
    src = MockBalanceSource(
        cumulative_df=pd.DataFrame({
            "block_date": [date(2026, 2, 1), date(2026, 3, 15)],
            "daily_net":  [5_000_000.0, 1_000_000.0],
            "cum_balance":[5_000_000.0, 6_000_000.0],
        }),
    )
    period = Period.from_month(Month(2026, 4), pin_blocks={Chain.ETHEREUM: 999_000})
    som_block = 990_000
    scale = 10 ** underlying.decimals
    # On-chain SoM = $25M → seed = $19M. On-chain EoM = $25M (same as the
    # post-shift tracked EoM = $6M + $19M) so the EoM cross-check stays
    # silent: this scenario is a pure pre-period funding gap.
    on_chain = {som_block: 25_000_000 * scale, 999_000: 25_000_000 * scale}
    balance_at = lambda c, t, h, b: on_chain[b]

    with caplog.at_level(logging.WARNING, logger="settle.normalize.balances"):
        df = get_subproxy_balance_timeseries(
            obex, Chain.ETHEREUM, underlying, period,
            source=src, som_block=som_block, balance_at=balance_at,
        )

    # SoM anchor warning fired with seed = $19M, EoM cross-check did NOT.
    msgs = [r.message for r in caplog.records]
    assert any("SoM anchor" in m and "19000000" in m.replace(",", "") for m in msgs)
    assert not any("EoM cross-check" in m for m in msgs)

    # Synthetic row at prime.start_date carrying the $19M seed.
    first = df.iloc[0]
    assert first["block_date"] == obex.start_date
    assert float(first["cum_balance"]) == pytest.approx(19_000_000.0)
    # Existing rows shifted up by $19M ($5M → $24M, $6M → $25M).
    by_date = {r["block_date"]: float(r["cum_balance"]) for _, r in df.iterrows()}
    assert by_date[date(2026, 2, 1)] == pytest.approx(24_000_000.0)
    assert by_date[date(2026, 3, 15)] == pytest.approx(25_000_000.0)


def test_subproxy_balance_no_anchor_when_seed_below_tolerance(config_dir: Path, caplog):
    """Sub-cent discrepancy between on-chain and Dune → no warning, no row
    shift. Guards against Decimal-precision dust triggering noise."""
    import logging
    obex = _obex(config_dir)
    venue = obex.venues[0]
    underlying = venue.underlying
    assert underlying is not None

    src = MockBalanceSource(
        cumulative_df=pd.DataFrame({
            "block_date": [date(2026, 2, 1)],
            "daily_net":  [5_000_000.0],
            "cum_balance":[5_000_000.0],
        }),
    )
    period = Period.from_month(Month(2026, 4), pin_blocks={Chain.ETHEREUM: 999_000})
    som_block = 990_000
    scale = 10 ** underlying.decimals
    # 0.001 token sub-unit drift — below the 0.01-token tolerance gate.
    sub_unit_drift = scale // 1_000  # = 0.001 token in raw wei
    on_chain = {som_block: 5_000_000 * scale + sub_unit_drift,
                999_000: 5_000_000 * scale + sub_unit_drift}
    balance_at = lambda c, t, h, b: on_chain[b]

    with caplog.at_level(logging.WARNING, logger="settle.normalize.balances"):
        df = get_subproxy_balance_timeseries(
            obex, Chain.ETHEREUM, underlying, period,
            source=src, som_block=som_block, balance_at=balance_at,
        )

    assert not caplog.records
    assert len(df) == 1
    assert df.iloc[0]["block_date"] == date(2026, 2, 1)


def test_subproxy_balance_eom_cross_check_fires_on_midperiod_drift(
    config_dir: Path, caplog,
):
    """SoM on-chain matches Dune (no seed) but on-chain EoM diverges → the
    EoM cross-check warning fires. Surfaces mid-period out-of-band transfers
    Dune missed."""
    import logging
    obex = _obex(config_dir)
    venue = obex.venues[0]
    underlying = venue.underlying
    assert underlying is not None

    src = MockBalanceSource(
        cumulative_df=pd.DataFrame({
            "block_date": [date(2026, 2, 1), date(2026, 3, 15)],
            "daily_net":  [5_000_000.0, 1_000_000.0],
            "cum_balance":[5_000_000.0, 6_000_000.0],
        }),
    )
    period = Period.from_month(Month(2026, 4), pin_blocks={Chain.ETHEREUM: 999_000})
    som_block = 990_000
    scale = 10 ** underlying.decimals
    # On-chain SoM = $6M (matches Dune cum_balance at SoM, no seed),
    # on-chain EoM = $9M (Dune-tracked EoM is $6M → $3M gap the SoM anchor
    # cannot explain because the missing transfer happened mid-period).
    on_chain = {som_block: 6_000_000 * scale, 999_000: 9_000_000 * scale}
    balance_at = lambda c, t, h, b: on_chain[b]

    with caplog.at_level(logging.WARNING, logger="settle.normalize.balances"):
        get_subproxy_balance_timeseries(
            obex, Chain.ETHEREUM, underlying, period,
            source=src, som_block=som_block, balance_at=balance_at,
        )

    msgs = [r.message for r in caplog.records]
    assert not any("SoM anchor found" in m for m in msgs)
    assert any("EoM cross-check found" in m and "3000000" in m.replace(",", "") for m in msgs)


def test_subproxy_balance_empty_df_with_seed_prepends_row_only(
    config_dir: Path, caplog,
):
    """Dune has zero rows but on-chain SoM > 0 (the Spark scenario verbatim)
    — the helper prepends a single synthetic row at ``prime.start_date``."""
    import logging
    obex = _obex(config_dir)
    venue = obex.venues[0]
    underlying = venue.underlying
    assert underlying is not None

    src = MockBalanceSource(cumulative_df=pd.DataFrame({
        "block_date": [], "daily_net": [], "cum_balance": [],
    }))
    period = Period.from_month(Month(2026, 4), pin_blocks={Chain.ETHEREUM: 999_000})
    som_block = 990_000
    scale = 10 ** underlying.decimals
    on_chain = {som_block: 36_000_000 * scale, 999_000: 36_000_000 * scale}
    balance_at = lambda c, t, h, b: on_chain[b]

    with caplog.at_level(logging.WARNING, logger="settle.normalize.balances"):
        df = get_subproxy_balance_timeseries(
            obex, Chain.ETHEREUM, underlying, period,
            source=src, som_block=som_block, balance_at=balance_at,
        )

    assert len(df) == 1
    assert df.iloc[0]["block_date"] == obex.start_date
    assert float(df.iloc[0]["cum_balance"]) == pytest.approx(36_000_000.0)
