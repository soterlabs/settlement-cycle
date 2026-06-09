"""Cat A ALM balance synthesis must be a DAILY series, not a SoM/EoM step.

A 2-row SoM/EoM frame makes ``cum_at_or_before`` return the SoM balance for
every interior day of the month, so the daily utilized deduction in
``compute_sky_revenue`` misses any mid-month swing in idle ALM balances
(~$700M+ of USDS/POL across Spark's chains). The loader now reads RPC
``balanceOf`` at the fixture's daily EoD blocks for interior days.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from settle.domain import Chain

_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def spark_and_fixtures():
    from tests.fixtures.spark_fixture_loader import load_spark_and_fixtures
    return load_spark_and_fixtures(_REPO)


def test_cat_a_synthesis_is_daily_not_two_point(
    spark_and_fixtures, monkeypatch: pytest.MonkeyPatch,
):
    from settle.extract import rpc as _rpc
    from tests.fixtures.spark_fixture_loader import (
        USDS_ETH,
        build_spark_sources,
    )

    spark, fixtures = spark_and_fixtures

    # Synthetic chain: raw balanceOf == block * 10^18, so the scaled USDS
    # balance equals the block number — lets the test assert exact values
    # per-day without any network access.
    monkeypatch.setattr(
        _rpc, "balance_of", lambda chain, token, holder, block: block * 10**18,
    )
    # The Cat B pre-period-anchor guard is out of scope for this test.
    monkeypatch.setenv("SETTLE_SPARK_ALLOW_PRE_PERIOD_ANCHOR", "1")

    som_pin, eom_pin = 11_111, 22_222
    sources = build_spark_sources(
        spark, fixtures,
        pin_blocks_som={Chain.ETHEREUM: som_pin},
        pin_blocks_eom={Chain.ETHEREUM: eom_pin},
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
    )

    eth_alm = spark.alm[Chain.ETHEREUM].value
    df = sources.balance.cumulative_balance_timeseries(
        "ethereum", USDS_ETH, eth_alm, date(2026, 2, 28), eom_pin,
    )

    # SoM anchor + interior days Mar 1..30 (all present in the eth block
    # fixture) + EoM anchor.
    assert len(df) == 32, f"expected 32 daily rows, got {len(df)}"
    assert df["block_date"].iloc[0] == date(2026, 2, 28)
    assert df["block_date"].iloc[-1] == date(2026, 3, 31)

    # SoM/EoM use the canonical pin blocks, not the fixture's daily blocks.
    assert df["cum_balance"].iloc[0] == Decimal(som_pin)
    assert df["cum_balance"].iloc[-1] == Decimal(eom_pin)

    # An interior day reads balanceOf at that day's fixture EoD block —
    # the value a 2-point step would never see.
    blocks = {
        (r["chain"], date.fromisoformat(r["block_date"])): r["block_number"]
        for r in fixtures["blocks_eth_ava"]["rows"]
    }
    mid = date(2026, 3, 15)
    mid_row = df[df["block_date"] == mid]
    assert len(mid_row) == 1
    assert mid_row["cum_balance"].iloc[0] == Decimal(blocks[("ethereum", mid)])

    # daily_net telescopes to the EoM balance.
    assert sum(df["daily_net"]) == df["cum_balance"].iloc[-1]
