"""The 2026-08-01 rate-convention cutover.

Why this file exists: the nominal-APR convention (#175) was applied
unconditionally, so re-running any month settled before it silently
re-priced that month. A Grove July re-run moved $39,939 from Grove to Sky
with nothing in the output saying why — the "new rules go forward only"
policy was enforced only by nobody pressing the button.

These tests cover the gate itself. The end-to-end guarantee — that every
settled month reproduces its committed artifacts — is verified by re-running
the per-prime runners and diffing ``settlements/``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from settle.compute._helpers import (
    APR_CONVENTION_START,
    apr_daily,
    apy_to_apr,
    combine_apys,
    compose_rate,
    daily_compounding_factor,
    daily_slice,
    uses_nominal_apr,
)

SSR = Decimal("0.0352")
SPREAD = Decimal("0.002")


# --- the boundary ----------------------------------------------------------

def test_cutover_is_2026_08_01():
    """August 2026 was the first cycle settled on the nominal convention."""
    assert APR_CONVENTION_START == date(2026, 8, 1)


@pytest.mark.parametrize("d,nominal", [
    (date(2026, 1, 1), False),
    (date(2026, 7, 22), False),   # spread still 30bps here — unrelated regime
    (date(2026, 7, 31), False),   # last pre-cutover day: July settles old
    (date(2026, 8, 1), True),     # first nominal day
    (date(2026, 9, 30), True),
])
def test_uses_nominal_apr_boundary(d, nominal):
    assert uses_nominal_apr(d) is nominal


# --- composition -----------------------------------------------------------

def test_compose_rate_pre_cutover_is_multiplicative_apy():
    """(1+SSR)(1+spread) − 1 = 3.72704% — what July and earlier were paid on."""
    r = compose_rate(SSR, SPREAD, date(2026, 7, 31))
    assert r == combine_apys(SSR, SPREAD)
    assert Decimal("0.0372703") < r < Decimal("0.0372705")


def test_compose_rate_post_cutover_is_nominal_sum():
    """apy_to_apr(SSR) + spread = 3.664456% — the convention from August."""
    r = compose_rate(SSR, SPREAD, date(2026, 8, 1))
    assert r == apy_to_apr(SSR) + SPREAD
    assert Decimal("0.0366445") < r < Decimal("0.0366446")


def test_the_two_conventions_differ_by_about_6_bps():
    """Not a rounding difference — 6.3 bps on the base rate, which is why the
    gate has to exist rather than being waved off as noise."""
    old = compose_rate(SSR, SPREAD, date(2026, 7, 31))
    new = compose_rate(SSR, SPREAD, date(2026, 8, 1))
    gap_bps = (old - new) * 10000
    assert Decimal("6.2") < gap_bps < Decimal("6.3")


def test_combine_apys_is_multiplicative_and_keeps_the_cross_term():
    assert combine_apys(SSR, SPREAD) == (1 + SSR) * (1 + SPREAD) - 1
    # strictly above the naive sum, by the cross-term
    assert combine_apys(SSR, SPREAD) > SSR + SPREAD
    assert combine_apys() == Decimal("0")


# --- accrual ---------------------------------------------------------------

def test_daily_slice_pre_cutover_is_the_apy_growth_factor():
    r = compose_rate(SSR, SPREAD, date(2026, 7, 31))
    assert daily_slice(r, date(2026, 7, 31)) == daily_compounding_factor(r)


def test_daily_slice_post_cutover_is_plain_division():
    r = compose_rate(SSR, SPREAD, date(2026, 8, 1))
    assert daily_slice(r, date(2026, 8, 1)) == apr_daily(r)
    assert daily_slice(r, date(2026, 8, 1)) == r / 365


def test_daily_slice_multiday_rejected_pre_cutover():
    """The APY factor compounds, so there is no linear multi-day form. Better
    to raise than to hand back a number nobody's policy produced."""
    with pytest.raises(ValueError, match="undefined before"):
        daily_slice(SSR, date(2026, 7, 31), 31)


def test_daily_slice_multiday_allowed_post_cutover():
    assert daily_slice(SSR, date(2026, 8, 1), 31) == SSR * 31 / 365


# --- wired into the compute path ------------------------------------------

def _flat_ssr():
    return pd.DataFrame({"effective_date": [date(2025, 1, 1)], "ssr_apy": [float(SSR)]})


def _balance(amount: str, since: date):
    return pd.DataFrame({
        "block_date": [since],
        "daily_net": [Decimal(amount)],
        "cum_balance": [Decimal(amount)],
    })


@pytest.mark.parametrize("start,end,nominal", [
    (date(2026, 7, 1), date(2026, 7, 31), False),
    (date(2026, 8, 1), date(2026, 8, 31), True),
])
def test_agent_rate_follows_the_convention_of_the_month_settled(start, end, nominal):
    """Same inputs, same 31-day length — only the month differs, and the
    result tracks whichever convention that month was settled on."""
    from settle.compute.agent_rate import AGENT_RATE_OVER_SSR, compute_agent_rate
    from settle.domain.period import Period

    empty = pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})
    principal = Decimal("10000000")
    total = compute_agent_rate(
        Period(start=start, end=end, pin_blocks={}),
        _balance(str(principal), date(2025, 12, 1)),
        empty,
        _flat_ssr(),
    )
    rate = compose_rate(SSR, AGENT_RATE_OVER_SSR, start)
    expected = principal * daily_slice(rate, start) * 31
    assert abs(total - expected) < Decimal("1e-6")
    # and the two regimes really do land on different dollars
    other = compose_rate(SSR, AGENT_RATE_OVER_SSR, date(2026, 8, 1) if not nominal else date(2026, 7, 1))
    assert abs(total - principal * daily_slice(other, date(2026, 8, 1) if not nominal else date(2026, 7, 1)) * 31) > Decimal("1")


def test_a_july_rerun_does_not_pick_up_the_nominal_convention():
    """Regression for the bug that motivated the gate: July must not re-price.

    Guards the wiring, not just the helper — if a future edit reintroduces an
    unconditional ``apy_to_apr`` in the agent-rate path, this fails.
    """
    from settle.compute.agent_rate import AGENT_RATE_OVER_SSR, compute_agent_rate
    from settle.domain.period import Period

    empty = pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})
    july = compute_agent_rate(
        Period(start=date(2026, 7, 1), end=date(2026, 7, 31), pin_blocks={}),
        _balance("10000000", date(2025, 12, 1)), empty, _flat_ssr(),
    )
    old_rate = combine_apys(SSR, AGENT_RATE_OVER_SSR)
    assert abs(july - Decimal("10000000") * daily_compounding_factor(old_rate) * 31) < Decimal("1e-6")
