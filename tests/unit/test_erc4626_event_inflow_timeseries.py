"""Unit tests for ``_erc4626_event_inflow_timeseries`` in
``settle.normalize.positions``.

The function reads ERC-4626 Deposit/Withdraw events from Dune
(``erc4626_centrifuge_flow.sql``) and returns a daily inflow DataFrame.
End-to-end SQL behaviour is exercised by
``tests/integration/test_centrifuge_flow_e2e.py``; here we stub
``execute_query`` and pin the Python-side data handling:

  1. raw assets/shares correctly aggregated into daily_inflow / net_shares
  2. ``underlying.decimals`` drives the divisor (6 for USDC, 18 for DAI)
  3. ``cum_inflow`` / ``cum_net_shares_raw`` accumulate in date order
  4. empty Dune result → empty DataFrame with the expected schema
  5. ``DuneError`` → graceful fallback to empty DataFrame (no raise)
  6. ``holder_override`` is forwarded to the Dune ``holder`` parameter
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from settle.domain import Address, Chain, Period, PricingCategory, Token, Venue
from settle.domain.primes import Prime
from settle.normalize.positions import _erc4626_event_inflow_timeseries

# ── fixtures ─────────────────────────────────────────────────────────────────

_VAULT  = Address.from_str("0x" + "cc" * 20)
_ALM    = Address.from_str("0x" + "dd" * 20)
_TOKEN  = Address.from_str("0x" + "ee" * 20)
_USDC   = Address.from_str("0x" + "11" * 20)
_DAI    = Address.from_str("0x" + "22" * 20)


def _usdc() -> Token:
    return Token(Chain.ETHEREUM, _USDC, "USDC", 6)


def _dai() -> Token:
    return Token(Chain.ETHEREUM, _DAI, "DAI", 18)


def _venue(*, underlying: Token | None, holder_override: Address | None = None) -> Venue:
    return Venue(
        id="E8",
        chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, _TOKEN, "JAAA", 6),
        pricing_category=PricingCategory.RWA_TRANCHE,
        centrifuge_vault=_VAULT,
        underlying=underlying,
        holder_override=holder_override,
        label="JAAA",
    )


def _prime() -> Prime:
    return Prime(
        id="grove",
        ilk_bytes32=b"\x00" * 32,
        start_date=date(2025, 5, 14),
        alm={Chain.ETHEREUM: _ALM},
    )


def _period() -> Period:
    return Period(
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
        pin_blocks={Chain.ETHEREUM: 24_781_026},
    )


def _stub_execute_query(monkeypatch, df: pd.DataFrame, *, recorder: list | None = None):
    """Replace settle.extract.dune.execute_query with a stub returning *df*."""
    def _stub(sql_path, *, params, pin_block):
        if recorder is not None:
            recorder.append((Path(sql_path).name, dict(params), pin_block))
        return df.copy()
    import settle.extract.dune as _dune
    monkeypatch.setattr(_dune, "execute_query", _stub)


# ── tests ────────────────────────────────────────────────────────────────────

def test_happy_path_usdc_decimals_and_cumulative_sums(monkeypatch, tmp_cache_dir):
    """Two days of vault events, USDC underlying (6 decimals).

    Day 1: 100 USDC deposit (+100 shares).
    Day 5: 80 USDC withdraw (−80 shares).
    Expected: daily_inflow [+100, −80], cum_inflow [+100, +20],
              daily_net_shares_raw [+100e6, −80e6], cum_net_shares_raw [+100e6, +20e6].
    """
    dune_df = pd.DataFrame({
        "block_date":     [date(2026, 3, 1), date(2026, 3, 5)],
        "assets_in_raw":  [100_000_000, 0],
        "assets_out_raw": [0,           80_000_000],
        "shares_in_raw":  [100_000_000, 0],
        "shares_out_raw": [0,            80_000_000],
    })
    _stub_execute_query(monkeypatch, dune_df)

    out = _erc4626_event_inflow_timeseries(
        _prime(), _venue(underlying=_usdc()), _period(), block_resolver=None,
    )

    # USDC decimals=6 → divisor 10^6; daily_inflow = (in − out) / 10^6
    assert list(out["daily_inflow"]) == [Decimal("100"), Decimal("-80")]
    assert list(out["cum_inflow"])   == [Decimal("100"), Decimal("20")]
    # shares are returned raw (no decimal scaling) for the share-balance check
    assert list(out["daily_net_shares_raw"]) == [Decimal("100000000"), Decimal("-80000000")]
    assert list(out["cum_net_shares_raw"])   == [Decimal("100000000"), Decimal("20000000")]
    # daily_assets_out is gross withdrawals (not net) — used for sd_share weighting
    assert list(out["daily_assets_out"]) == [Decimal("0"), Decimal("80")]


def test_dai_underlying_uses_18_decimal_divisor(monkeypatch, tmp_cache_dir):
    """DAI underlying (18 decimals) → divisor 10^18.  Pins that the function
    reads ``venue.underlying.decimals`` rather than hardcoding 6."""
    raw = 12_345_678_900_000_000_000  # 12.3456789 DAI
    dune_df = pd.DataFrame({
        "block_date":     [date(2026, 3, 10)],
        "assets_in_raw":  [raw],
        "assets_out_raw": [0],
        "shares_in_raw":  [0],
        "shares_out_raw": [0],
    })
    _stub_execute_query(monkeypatch, dune_df)

    out = _erc4626_event_inflow_timeseries(
        _prime(), _venue(underlying=_dai()), _period(), block_resolver=None,
    )
    assert out.loc[0, "daily_inflow"] == Decimal("12.3456789")


def test_missing_underlying_falls_back_to_six_decimals(monkeypatch, tmp_cache_dir):
    """``venue.underlying = None`` → divisor defaults to 10^6 (safe for all
    current Centrifuge USDC vaults).  Pins the documented fallback so a
    future refactor doesn't silently change the divisor."""
    dune_df = pd.DataFrame({
        "block_date":     [date(2026, 3, 1)],
        "assets_in_raw":  [50_000_000],
        "assets_out_raw": [0],
        "shares_in_raw":  [0],
        "shares_out_raw": [0],
    })
    _stub_execute_query(monkeypatch, dune_df)

    out = _erc4626_event_inflow_timeseries(
        _prime(), _venue(underlying=None), _period(), block_resolver=None,
    )
    assert out.loc[0, "daily_inflow"] == Decimal("50")


def test_empty_dune_result_returns_empty_with_expected_columns(monkeypatch, tmp_cache_dir):
    """No vault activity → empty DataFrame with the schema downstream code
    expects (block_date, daily_inflow, cum_inflow, daily_net_shares_raw,
    cum_net_shares_raw).  An undefined schema would break consumers that
    iterate columns even on empty input."""
    _stub_execute_query(monkeypatch, pd.DataFrame())

    out = _erc4626_event_inflow_timeseries(
        _prime(), _venue(underlying=_usdc()), _period(), block_resolver=None,
    )
    assert out.empty
    expected_cols = {
        "block_date", "daily_inflow", "daily_assets_out",
        "cum_inflow", "daily_net_shares_raw", "cum_net_shares_raw",
    }
    assert expected_cols.issubset(set(out.columns))


def test_dune_error_falls_back_to_empty_and_warns(monkeypatch, tmp_cache_dir, caplog):
    """``DuneError`` from execute_query → empty DataFrame + WARNING (so the
    rest of compute_monthly_pnl continues with revenue = Δvalue rather than
    crashing the whole pipeline)."""
    import logging
    import settle.extract.dune as _dune

    def _raise(*_a, **_kw):
        raise _dune.DuneError("execution failed: stubbed")

    monkeypatch.setattr(_dune, "execute_query", _raise)

    caplog.set_level(logging.WARNING)
    out = _erc4626_event_inflow_timeseries(
        _prime(), _venue(underlying=_usdc()), _period(), block_resolver=None,
    )
    assert out.empty
    assert any(
        "Dune query failed" in r.getMessage() and r.levelname == "WARNING"
        for r in caplog.records
    )


def test_holder_override_forwarded_to_dune_params(monkeypatch, tmp_cache_dir):
    """When venue.holder_override is set, the query receives the override
    (not prime.alm) as the holder param."""
    override = Address.from_str("0x" + "99" * 20)
    recorder: list = []
    _stub_execute_query(monkeypatch, pd.DataFrame(), recorder=recorder)

    _erc4626_event_inflow_timeseries(
        _prime(),
        _venue(underlying=_usdc(), holder_override=override),
        _period(),
        block_resolver=None,
    )

    assert len(recorder) == 1
    _, params, _ = recorder[0]
    assert params["holder"] == override.value
    assert params["holder"] != _ALM.value


def test_pin_block_and_vault_forwarded_to_dune_params(monkeypatch, tmp_cache_dir):
    """``pin_block`` derives from ``period.pin_blocks[venue.chain]`` and
    ``vault`` comes from ``venue.centrifuge_vault.value`` — the two most
    likely places to silently regress when wiring is changed."""
    recorder: list = []
    _stub_execute_query(monkeypatch, pd.DataFrame(), recorder=recorder)

    _erc4626_event_inflow_timeseries(
        _prime(), _venue(underlying=_usdc()), _period(), block_resolver=None,
    )

    assert len(recorder) == 1
    name, params, pin_block = recorder[0]
    assert name == "erc4626_centrifuge_flow.sql"
    assert params["vault"]  == _VAULT.value
    assert params["holder"] == _ALM.value
    assert params["start_date"] == "2025-05-14"
    assert pin_block == 24_781_026
