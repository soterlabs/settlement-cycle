"""Chronicle Points — dash-formula fidelity + gating."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from settle.compute.chronicle_points import compute_chronicle_points
from settle.domain.period import Period


def _period(y, m, d1, d2):
    return Period(start=date(y, m, d1), end=date(y, m, d2), pin_blocks={})


def _ssr(apy="0.0352"):
    return pd.DataFrame({"effective_date": [date(2024, 9, 1)],
                         "ssr_apy": [Decimal(apy)]})


def _farm(rows):
    return pd.DataFrame({
        "block_date": [date.fromisoformat(d) for d, _ in rows],
        "daily_net": [Decimal(v) for _, v in rows],
        "cum_balance": [Decimal(v) for _, v in rows],
    })


def test_formula_is_nominal_share_of_the_base_rate():
    """``balance × 0.20 × (apy_to_apr(SSR, 12) + spread) / 365`` — the same
    NOMINAL convention as the Base Rate this is a 20% share of
    (2026-09-01). The dash's own series compounds and adds the raw SSR APY,
    so the two deliberately no longer reconcile."""
    from settle.compute._helpers import apr_daily, apy_to_apr
    farm = _farm([("2026-06-01", "10000000")])
    out = compute_chronicle_points(_period(2026, 6, 1, 1), farm, _ssr("0.0360"))
    eff = Decimal("0.20") * (apy_to_apr(Decimal("0.0360")) + Decimal("0.003"))
    assert abs(out - Decimal("10000000") * apr_daily(eff)) < Decimal("1e-9")


def test_carry_forward_ssr_step_and_spread_step():
    """Quiet days carry the balance; on 2026-07-23 BOTH the SSR (3.60→3.52)
    and the dated spread (30bps→20bps) step — the accrual must use
    0.20×(apr(3.60%)+30bps) for Jul 1–22 and 0.20×(apr(3.52%)+20bps) from
    Jul 23."""
    farm = _farm([("2026-07-01", "10000000")])
    ssr = pd.DataFrame({
        "effective_date": [date(2024, 9, 1), date(2026, 7, 23)],
        "ssr_apy": [Decimal("0.0360"), Decimal("0.0352")],
    })
    from settle.compute._helpers import apr_daily, apy_to_apr
    out = compute_chronicle_points(_period(2026, 7, 1, 31), farm, ssr)
    f1 = apr_daily(Decimal("0.20") * (apy_to_apr(Decimal("0.0360")) + Decimal("0.003")))
    f2 = apr_daily(Decimal("0.20") * (apy_to_apr(Decimal("0.0352")) + Decimal("0.002")))
    assert abs(out - Decimal("10000000") * (22 * f1 + 9 * f2)) < Decimal("1e-9")


def test_zero_balance_earns_nothing():
    farm = _farm([("2026-07-10", "0")])
    assert compute_chronicle_points(_period(2026, 7, 1, 31), farm, _ssr()) == 0
