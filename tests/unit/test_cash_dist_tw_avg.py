"""Cash-distribution venues need a dated capital series for time-weighting.

Their revenue comes from ``actual_revenue_override``, so the SoM/EoM residual
formula is bypassed — but ``tw_avg_value`` is still derived from ``value_som``
plus the inflow series (``prime_agent_revenue._time_weighted_avg_value``). With
an empty series the venue looks flat at its START-of-month value all period.

Grove E21 (Galaxy GACLO-1), 2026-08: the position stepped down $9,679,121.58 on
Aug 19 (two redemptions 21 minutes apart), but tw_avg read $27,586,956.37
instead of $23,527,969.90 — drawing $85,288.92 of cost-of-funds attribution
against a true ~$72,985 and diluting the excess away from every other venue.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from settle.compute.prime_agent_revenue import (
    VenueRevenueInputs,
    _time_weighted_avg_value,
)
from settle.domain.period import Period
from settle.domain.primes import Chain


class _V:
    id = "E21"


_PERIOD = Period(date(2026, 8, 1), date(2026, 8, 31), {Chain.AVALANCHE_C: 1})
_SOM = Decimal("27586956.37")


def _empty():
    return pd.DataFrame(columns=["block_date", "daily_inflow", "cum_inflow"])


def _dated():
    """The Aug 19 step-down, as cum_balance_e21 actually records it."""
    df = pd.DataFrame({
        "block_date": [date(2026, 8, 19)],
        "daily_inflow": [Decimal("-9679121.58")],
    })
    df["cum_inflow"] = df["daily_inflow"].cumsum()
    return df


def test_empty_series_pins_the_average_at_start_of_month():
    """The pre-fix behaviour — pinned here so a regression is visible."""
    got = _time_weighted_avg_value(_PERIOD, _SOM, _empty(), venue_id="E21")
    assert got == _SOM


def test_dated_series_reflects_the_mid_period_step_down():
    got = _time_weighted_avg_value(_PERIOD, _SOM, _dated(), venue_id="E21")
    # 18 days at 27,586,956.37 then 13 at 17,907,834.79
    expected = (_SOM * 18 + (_SOM - Decimal("9679121.58")) * 13) / 31
    assert abs(got - expected) < Decimal("0.01"), (got, expected)
    assert got < _SOM, "a redemption must pull the average DOWN"
    assert Decimal("23000000") < got < Decimal("24000000"), got


def test_the_override_still_pins_revenue_regardless_of_the_series():
    """Supplying a real series must not let the residual formula move revenue —
    that is the whole reason it is safe to add one.

    Uses the real E21 venue rather than a stub so the assertion holds against
    the actual config, flags and all.
    """
    from pathlib import Path

    from settle.compute.prime_agent_revenue import compute_venue_revenue
    from settle.domain.config import load_prime

    e21 = next(v for v in load_prime(Path("config/grove.yaml")).venues
               if v.id == "E21")
    period = Period(date(2026, 8, 1), date(2026, 8, 31), {e21.chain: 1})
    override = Decimal("222936.27")
    out = {}
    for label, df in (("empty", _empty()), ("dated", _dated())):
        r = compute_venue_revenue(period, VenueRevenueInputs(
            venue=e21, value_som=_SOM,
            value_eom=_SOM - Decimal("9679121.58"),
            inflow_timeseries=df, actual_revenue_override=override,
        ))
        out[label] = (r.actual_revenue, r.tw_avg_value)
    assert out["empty"][0] == out["dated"][0] == override, out
    assert out["empty"][1] == _SOM, "empty series pins tw_avg at SoM"
    assert out["dated"][1] < _SOM, "a dated redemption pulls tw_avg down"
