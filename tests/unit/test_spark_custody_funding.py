import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("scripts").resolve()))
from analyze_spark_custody_funding import analyze


def test_savings_and_unknown_value_are_separate_from_sky_principal():
    day = "2026-09-01"
    custody = {
        day: {
            "psm:arbitrum:pool:holder": {
                "sky_principal": "60",
                "savings_by_source": {"vault": "30"},
                "observed_value": "99",
                "uncertain": True,
            },
            "financing:saver-interest:vault": {
                "sky_principal": "5",
                "savings_by_source": {},
                "observed_value": "0",
                "uncertain": False,
            },
        }
    }
    psm = {
        "arbitrum": {
            "daily": [{"block_date": day, "cum_balance": "100"}],
            "share_control": {"closing_shares_rpc": 100, "closing_shares_events": 100},
        }
    }
    control = {"sky_revenue_daily": [{"date": day, "daily_sky_rev": "1", "utilized": "100"}]}
    result = analyze(custody, psm, control)
    totals = result["psm_totals"]
    assert totals["sky"] == Decimal("0.6")
    assert totals["savings"] == Decimal("0.3")
    assert totals["unattributed_value"] == Decimal("0.1")
    assert result["saver_interest_sky_cost"] == Decimal("0.05")
    with pytest.raises(ValueError, match="Missing custody"):
        analyze({}, psm, control)
    psm["arbitrum"]["share_control"]["closing_shares_rpc"] = 99
    with pytest.raises(ValueError, match="closing shares"):
        analyze(custody, psm, control)
