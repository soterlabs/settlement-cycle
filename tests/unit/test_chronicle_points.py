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


def test_formula_matches_dash_verbatim():
    """balance × ((1 + 0.20×(SSR+0.003))^(1/365) − 1), ADDITIVE spread —
    NOT the repo's multiplicative ⊕ and NOT the dated 20bps schedule."""
    farm = _farm([("2026-07-01", "10000000")])
    out = compute_chronicle_points(_period(2026, 7, 1, 1), farm, _ssr("0.0352"))
    eff = 0.20 * (0.0352 + 0.003)
    expected = Decimal("10000000") * Decimal(str((1 + eff) ** (1 / 365) - 1))
    assert out == expected


def test_carry_forward_and_ssr_step():
    """Quiet days carry the balance; the SSR step changes the rate mid-month."""
    farm = _farm([("2026-07-01", "10000000")])
    ssr = pd.DataFrame({
        "effective_date": [date(2024, 9, 1), date(2026, 7, 23)],
        "ssr_apy": [Decimal("0.0360"), Decimal("0.0352")],
    })
    out = compute_chronicle_points(_period(2026, 7, 1, 31), farm, ssr)
    f1 = Decimal(str((1 + 0.20 * (0.0360 + 0.003)) ** (1 / 365) - 1))
    f2 = Decimal(str((1 + 0.20 * (0.0352 + 0.003)) ** (1 / 365) - 1))
    assert out == Decimal("10000000") * (22 * f1 + 9 * f2)


def test_zero_balance_earns_nothing():
    farm = _farm([("2026-07-10", "0")])
    assert compute_chronicle_points(_period(2026, 7, 1, 31), farm, _ssr()) == 0
