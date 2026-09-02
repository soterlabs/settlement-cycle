"""Cat A all-capital series must keep real dates when only the
per-counterparty log is captured.

Grove E41 (JTRSY Basin escrow) is the motivating case: no ``cum_balance``
capture, a complete ``inflow_by_counterparty`` one. Without the fallback the
whole period Δvalue collapses onto a single ``period.start`` residual row, so
the venue looks like it held its end-of-month balance from day 1. Revenue is
unaffected ($0 either way) but the time-weighted average — the base cost of
funds is allocated on — is badly overstated.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from settle.domain.period import Period
from settle.domain.primes import Address, Chain
from settle.normalize.positions import _cat_a_all_capital_inflow_timeseries


class _Prime:
    start_date = date(2026, 1, 1)

    @property
    def alm(self):
        return {Chain.ETHEREUM: Address.from_str("0x" + "11" * 20)}


class _Tok:
    address = Address.from_str("0xdc035d45d973e3ec169d2276ddab16f1e407384f")  # USDS
    symbol = "USDS"
    decimals = 18


class _Venue:
    id = "E41"
    chain = Chain.ETHEREUM
    token = _Tok()
    holder_override = Address.from_str("0x2cd296095788a2741e72056d66b3ae1faee23ea2")


_PERIOD = Period(date(2026, 8, 1), date(2026, 8, 31), {Chain.ETHEREUM: 25878704})

# E41's real August flows, as captured in inflow_by_counterparty.
_CP_ROWS = [
    ("2026-08-18", "4000000.0"),
    ("2026-08-28", "2000000.0"),
    ("2026-08-29", "1977510.008656"),
    ("2026-08-31", "3522489.9913439997"),
]


class _Src:
    """cum_balance empty; counterparty log populated."""

    def __init__(self, cp_rows=_CP_ROWS, cum_rows=None):
        self._cp, self._cum = cp_rows, cum_rows

    def cumulative_balance_timeseries(self, **kw):
        if self._cum is None:
            return pd.DataFrame({"block_date": [], "daily_net": []})
        return pd.DataFrame(
            {"block_date": [r[0] for r in self._cum],
             "daily_net": [r[1] for r in self._cum]}
        )

    def inflow_by_counterparty(self, **kw):
        return pd.DataFrame(
            {"block_date": [r[0] for r in self._cp],
             "counterparty": ["0x" + "22" * 20] * len(self._cp),
             "signed_amount": [r[1] for r in self._cp]}
        )


_EXACT_SUM = "11499999.9999999997"   # the four real rows, summed exactly


def _run(src, target_delta=_EXACT_SUM):
    return _cat_a_all_capital_inflow_timeseries(
        _Prime(), _Venue(), _PERIOD,
        balance_source=src, target_delta=Decimal(target_delta),
    )


def test_flows_keep_their_real_dates():
    df = _run(_Src())
    got = {str(d): Decimal(str(v)) for d, v in
           zip(df["block_date"], df["daily_inflow"], strict=True)}
    assert got == {
        "2026-08-18": Decimal("4000000.0"),
        "2026-08-28": Decimal("2000000.0"),
        "2026-08-29": Decimal("1977510.008656"),
        "2026-08-31": Decimal("3522489.9913439997"),
    }
    assert "2026-08-01" not in got, "must not re-time flows to period start"


def test_no_residual_row_when_the_counterparty_log_is_complete():
    """Σ counterparty = target_delta exactly, so the backstop adds nothing."""
    df = _run(_Src())
    assert sum((Decimal(str(v)) for v in df["daily_inflow"]), Decimal(0)) \
        == Decimal(_EXACT_SUM)
    assert len(df) == 4, "no residual row when the log already reconciles"


def test_residual_backstop_still_fires_on_an_incomplete_log():
    """Revenue must stay $0 even when the counterparty log is partial — the
    missing piece lands at period start, as before."""
    df = _run(_Src(cp_rows=_CP_ROWS[:2]), target_delta="11500000")  # 6M of 11.5M captured
    got = dict(zip([str(d) for d in df["block_date"]],
                   [Decimal(str(v)) for v in df["daily_inflow"]], strict=True))
    assert got["2026-08-01"] == Decimal("5500000.0")
    assert got["2026-08-18"] == Decimal("4000000.0")


def test_cum_balance_wins_when_present():
    """The fallback must not displace a real cumulative-balance capture."""
    df = _run(_Src(cum_rows=[("2026-08-05", "11500000.0")]), target_delta="11500000")
    assert [str(d) for d in df["block_date"]] == ["2026-08-05"]


def test_missing_counterparty_method_degrades_to_the_old_behaviour():
    class _Bare:
        def cumulative_balance_timeseries(self, **kw):
            return pd.DataFrame({"block_date": [], "daily_net": []})
    df = _cat_a_all_capital_inflow_timeseries(
        _Prime(), _Venue(), _PERIOD,
        balance_source=_Bare(), target_delta=Decimal("11500000"),
    )
    assert [str(d) for d in df["block_date"]] == ["2026-08-01"]
