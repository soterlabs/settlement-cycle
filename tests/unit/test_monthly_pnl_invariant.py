from dataclasses import fields
from datetime import date
from decimal import Decimal

import pytest

from settle.domain.monthly_pnl import MonthlyPnL
from settle.domain.period import Month, Period


def _monthly_pnl(*, stored: Decimal, expected_prime_revenue: Decimal) -> MonthlyPnL:
    """Build the smallest valid MonthlyPnL while insulating this test from
    unrelated required fields added to the report model."""
    values = {
        "prime_id": "spark",
        "month": Month(2026, 9),
        "period": Period(
            start=date(2026, 9, 1),
            end=date(2026, 9, 30),
            pin_blocks={},
        ),
        "sky_revenue": Decimal("0"),
        "agent_rate": Decimal("0"),
        "prime_agent_revenue": expected_prime_revenue,
        "chronicle_points": Decimal("0"),
        "gar": Decimal("0"),
        "monthly_pnl": stored,
        "venue_breakdown": [],
        "pin_blocks_som": {},
    }
    required = {f.name for f in fields(MonthlyPnL) if f.default is f.default_factory}
    missing = required - values.keys()
    assert not missing, f"test helper needs values for new required fields: {missing}"
    return MonthlyPnL(**values)


def test_monthly_pnl_invariant_accepts_sub_nanodollar_decimal_noise():
    _monthly_pnl(
        stored=Decimal("4036298.350171028036893576915"),
        expected_prime_revenue=Decimal("4036298.350171028036893576916"),
    )


def test_monthly_pnl_invariant_rejects_material_mismatch():
    with pytest.raises(ValueError, match="monthly_pnl invariant broken"):
        _monthly_pnl(stored=Decimal("1"), expected_prime_revenue=Decimal("1.00000001"))
