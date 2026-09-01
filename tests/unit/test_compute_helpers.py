"""Unit tests for `settle.compute._helpers`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from settle.compute._helpers import (
    apr_daily,
    apy_to_apr,
    cum_at_or_before,
    daily_compounding_factor,
    ssr_at_or_before,
)


# --- apy_to_apr / apr_daily -----------------------------------------------

def test_apy_to_apr_known_value():
    """SSR 3.52% APY at n=12 -> 3.464456% APR, so BR_apr = 3.664456%."""
    apr = apy_to_apr(Decimal("0.0352"))
    assert Decimal("0.0346445") < apr < Decimal("0.0346446")
    assert Decimal("0.0366445") < apr + Decimal("0.002") < Decimal("0.0366446")


def test_apy_to_apr_round_trips_at_the_same_n():
    """The conversion is exact iff you compound back at the SAME n. n=12 is
    chosen because the charge compounds monthly (MSC debt capitalisation)."""
    apy = Decimal("0.0352")
    for n in (1, 12, 365):
        apr = apy_to_apr(apy, n)
        back = (1 + apr / n) ** n - 1
        assert abs(back - apy) < Decimal("1e-12"), n


def test_apy_to_apr_converging_to_ln_as_n_grows():
    """n -> infinity gives ln(1+APY); n=12 sits ~0.5 bps above it, which is
    the whole reason n must match the settlement cadence."""
    import math
    apy = Decimal("0.0352")
    ln = Decimal(str(math.log(1 + float(apy))))
    assert abs(apy_to_apr(apy, 31_536_000) - ln) < Decimal("1e-9")
    gap = apy_to_apr(apy, 12) - ln
    assert Decimal("0.00004") < gap < Decimal("0.00006")      # ~0.50 bps


def test_apy_to_apr_rejects_bad_n():
    with pytest.raises(ValueError, match="n must be"):
        apy_to_apr(Decimal("0.0352"), 0)


def test_idle_susds_legs_net_to_zero_over_a_year():
    """Rule 5 in settled dollars: a prime holding sUSDS financed 1:1 by debt
    should net ~zero across credit − BR + reimbursement over a year.

    This must be simulated over the full cycle, not compared slice-by-slice:
    the credit accrues on a balance compounding CONTINUOUSLY (the index)
    while the charge accrues on a debt that is static within the month and
    STEPS UP monthly as the MSC capitalises the net charge. n=12 is exactly
    the conversion that makes those two paths reach the same annual total.

    An earlier version of this test compared the daily slices on a static
    balance, "found" a −0.48 bps/yr residual and asserted it. That residual
    is an artefact of ignoring the capitalisation — see PRD §17.13.
    """
    from settle.compute._helpers import daily_compounding_factor
    ssr, spread = Decimal("0.0352"), Decimal("0.002")
    ssr_apr = apy_to_apr(ssr)
    base = ssr_apr + spread
    f = daily_compounding_factor(ssr)
    days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

    v = debt = start = Decimal("1000000000")
    credit = charge = refund = Decimal("0")
    for n in days:
        for _ in range(n):
            v *= 1 + f                       # index compounds daily
            credit += v * f                  # appreciation leg, on the MtM value
        charge += debt * apr_daily(base, n)
        refund += debt * apr_daily(spread, n)
        debt += debt * apr_daily(ssr_apr, n)  # MSC capitalises the NET charge

    net_bps = (credit - charge + refund) / start * 10000
    assert abs(net_bps) < Decimal("0.1"), f"Rule 5 broke: {net_bps} bps/yr"
    # both principals land on the SSR APY, by different routes
    assert abs((v / start - 1) - ssr) < Decimal("1e-9")
    assert abs((debt / start - 1) - ssr) < Decimal("1e-6")


def test_apr_daily_is_plain_slicing():
    apr = Decimal("0.0366")
    assert apr_daily(apr) == apr / 365
    assert apr_daily(apr, 31) == apr * 31 / 365
    # nominal: 31 daily slices == one 31-day slice (no compounding)
    assert abs(apr_daily(apr) * 31 - apr_daily(apr, 31)) < Decimal("1e-25")


# --- daily_compounding_factor ---------------------------------------------

def test_daily_factor_zero_apy():
    assert daily_compounding_factor(Decimal("0")) == Decimal("0.0")


def test_daily_factor_5_pct_apy():
    """((1.05)^(1/365) - 1) ≈ 0.00013368... — verified independently."""
    f = daily_compounding_factor(Decimal("0.05"))
    assert Decimal("0.000133") < f < Decimal("0.000135")


def test_daily_factor_compounds_to_apy_over_365_days():
    """Compounding the factor 365× should reproduce APY (within float precision)."""
    apy = Decimal("0.045")
    f = daily_compounding_factor(apy)
    grown = (1 + float(f)) ** 365 - 1
    assert abs(grown - 0.045) < 1e-10


# --- cum_at_or_before ------------------------------------------------------

def _ts():
    return pd.DataFrame({
        "block_date": [date(2025, 11, 17), date(2025, 12, 1), date(2026, 2, 2)],
        "cum_balance": [21_000_000.0, 25_000_000.0, 25_442_327.0],
    })


def test_cum_at_or_before_returns_latest_at_target():
    assert cum_at_or_before(_ts(), "cum_balance", date(2025, 11, 17)) == Decimal("21000000.0")
    assert cum_at_or_before(_ts(), "cum_balance", date(2025, 12, 31)) == Decimal("25000000.0")
    assert cum_at_or_before(_ts(), "cum_balance", date(2026, 2, 2)) == Decimal("25442327.0")
    assert cum_at_or_before(_ts(), "cum_balance", date(2026, 4, 1)) == Decimal("25442327.0")


def test_cum_at_or_before_returns_zero_for_pre_history():
    assert cum_at_or_before(_ts(), "cum_balance", date(2025, 1, 1)) == Decimal("0")


def test_cum_at_or_before_handles_empty_dataframe():
    empty = pd.DataFrame({"block_date": [], "cum_balance": []})
    assert cum_at_or_before(empty, "cum_balance", date(2026, 4, 1)) == Decimal("0")


def test_cum_at_or_before_handles_none():
    assert cum_at_or_before(None, "cum_balance", date(2026, 4, 1)) == Decimal("0")


def test_cum_at_or_before_with_decimal_value_column():
    """Production Dune sources emit `Decimal` (not `float`) in `cum_*` columns
    after fix I4. Ensure carry-forward still works when values are Decimals,
    not floats — locks in the production-source dtype contract."""
    df = pd.DataFrame({
        "block_date":  [date(2025, 11, 17), date(2026, 2, 2)],
        "cum_balance": [Decimal("21000000"), Decimal("21442327.123456789")],
    })
    # High-precision value preserved end-to-end (no float intermediate).
    out = cum_at_or_before(df, "cum_balance", date(2026, 3, 1))
    assert out == Decimal("21442327.123456789")
    assert isinstance(out, Decimal)


def test_cum_at_or_before_unsorted_input():
    """If the source returned rows out of date order, ``idxmax`` still finds the
    row with the largest date ≤ target."""
    df = pd.DataFrame({
        "block_date":  [date(2026, 3, 30), date(2025, 11, 17), date(2026, 2, 2)],
        "cum_balance": [25_000_000.0,      21_000_000.0,        22_000_000.0],
    })
    assert cum_at_or_before(df, "cum_balance", date(2026, 3, 1)) == Decimal("22000000.0")
    assert cum_at_or_before(df, "cum_balance", date(2026, 4, 1)) == Decimal("25000000.0")


def test_cum_at_or_before_duplicate_dates_takes_last_row():
    """Multiple rows on the max date → the positionally LAST one wins.

    Regression: E3 April 2026 had two per-event inflow rows on Apr 24
    (Merkl claim in, full burn out). The old ``idxmax`` lookup returned
    the FIRST tied row, dropping the burn from the cumulative and
    booking a phantom −$1.41M principal loss."""
    df = pd.DataFrame({
        "block_date": [date(2026, 4, 17), date(2026, 4, 24), date(2026, 4, 24)],
        "cum_inflow": [Decimal("-25000000"),
                       Decimal("-136919507.91"),   # after Merkl claim in
                       Decimal("-138331420.20")],  # after same-day burn out
    })
    assert cum_at_or_before(df, "cum_inflow", date(2026, 4, 30)) == Decimal("-138331420.20")
    assert cum_at_or_before(df, "cum_inflow", date(2026, 4, 24)) == Decimal("-138331420.20")
    assert cum_at_or_before(df, "cum_inflow", date(2026, 4, 20)) == Decimal("-25000000")


# --- ssr_at_or_before ------------------------------------------------------

def _ssr():
    return pd.DataFrame({
        "effective_date": [date(2025, 12, 2), date(2025, 12, 16), date(2026, 3, 9)],
        "ssr_apy":        [0.0425,            0.0400,             0.0375],
    })


def test_ssr_at_or_before_returns_latest_change():
    assert ssr_at_or_before(_ssr(), date(2025, 12, 2)) == Decimal("0.0425")
    assert ssr_at_or_before(_ssr(), date(2025, 12, 31)) == Decimal("0.04")
    assert ssr_at_or_before(_ssr(), date(2026, 3, 9)) == Decimal("0.0375")
    assert ssr_at_or_before(_ssr(), date(2026, 4, 30)) == Decimal("0.0375")


def test_ssr_at_or_before_raises_when_no_prior_change():
    with pytest.raises(ValueError, match="No SSR change at or before"):
        ssr_at_or_before(_ssr(), date(2025, 11, 1))


def test_ssr_at_or_before_raises_when_history_empty():
    empty = pd.DataFrame({"effective_date": [], "ssr_apy": []})
    with pytest.raises(ValueError, match="empty"):
        ssr_at_or_before(empty, date(2026, 3, 1))
