from decimal import Decimal

import pandas as pd

from scripts.compare_hypersync_venue import compare_frames


def test_parity_catches_offsetting_daily_errors_with_equal_final_balance():
    d = pd.DataFrame([{"block_date": "2026-08-01", "daily_net": 10, "cum_balance": 10},
                      {"block_date": "2026-08-02", "daily_net": -10, "cum_balance": 0}])
    h = pd.DataFrame([{"block_date": "2026-08-01", "daily_net": 5, "cum_balance": 5},
                      {"block_date": "2026-08-02", "daily_net": -5, "cum_balance": 0}])
    result = compare_frames("test", d, h, ["block_date"], ["daily_net", "cum_balance"], Decimal(0))
    assert not result["matched"]
    assert result["mismatch_count"] == 2


def test_sparse_cumulative_rows_carry_forward_on_quiet_days():
    d = pd.DataFrame([{"block_date": "2026-08-01", "daily_net": 10, "cum_balance": 10}])
    h = pd.concat([d, pd.DataFrame([{"block_date": "2026-08-02", "daily_net": 0, "cum_balance": 10}])])
    assert compare_frames("test", d, h, ["block_date"], ["daily_net", "cum_balance"], Decimal(0))["matched"]
