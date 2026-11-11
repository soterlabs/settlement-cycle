import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("scripts").resolve()))
from export_spark_venue_borrowing_costs import build


def test_acknowledged_gap_is_never_spread_to_venues():
    costs = {
        "start": "2026-09-01",
        "end": "2026-09-30",
        "allocations": [
            {
                "venue": "S1",
                "conditional_sky_borrowing_cost": "100",
                "daily": {
                    f"2026-09-{d:02d}": {
                        "sky_principal": "1000",
                        "savings_principal": "200",
                        "uncertain": True,
                    }
                    for d in range(1, 31)
                },
            }
        ],
    }
    reconciliation = {
        "existing_conditional_allocation_cost": "100",
        "additional_cost_rows": [{"cost_of_funds": "-10"}],
        "allocation_cost_including_excess_credits": "90",
        "global_cost": "150",
        "separate_financing_costs": {"msc": "20"},
        "unassigned_cost": "40",
    }
    custody = {"psm_totals": {"unattributed_value": "26"}}
    result = build(costs, reconciliation, custody, {"S1": "Position", "S71": "Unpriced"})
    assert result["allocations"][0]["borrowing_cost_usd"] == D(100)
    assert result["allocations"][1]["borrowing_cost_usd"] is None
    assert result["acknowledged_psm3_unattributed_value_cost_equivalent_usd"] == "26"
    assert result["subtotal_after_separate_credits_usd"] == D(90)
    reconciliation["existing_conditional_allocation_cost"] = "101"
    with pytest.raises(ValueError, match="subtotal"):
        build(costs, reconciliation, custody, {"S1": "Position"})
