from decimal import Decimal

import pandas as pd
import pytest

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


@pytest.mark.parametrize("raw,expected", [(100000000, True), (100000001, False)])
def test_precision_exception_requires_exact_raw_parity(monkeypatch, raw, expected):
    from scripts import compare_hypersync_venue as mod
    dune = pd.DataFrame([{"day": "2026-08-01", "daily_net": Decimal("100.000002")}])
    hs = pd.DataFrame([{"day": "2026-08-01", "daily_net": Decimal("100")}], index=[7])
    monkeypatch.setattr(mod, "execute_query", lambda *a: pd.DataFrame([{"day": "2026-08-01", "daily_net": raw}]))
    check = mod.compare_with_raw_precision_check("legacy", dune, hs, ["day"], ["daily_net"], Decimal("0.000001"),
                raw_sql="oracle.sql", raw_params={}, pin_block=100, decimals=6)
    assert check["matched"] is expected
    assert not check["legacy_float_comparison"]["matched"]
    assert check["tolerance"] == "0"


def test_material_difference_cannot_use_precision_exception(monkeypatch):
    from scripts import compare_hypersync_venue as mod
    dune = pd.DataFrame([{"day": "2026-08-01", "daily_net": Decimal("100.01")}])
    hs = pd.DataFrame([{"day": "2026-08-01", "daily_net": Decimal("100")}])
    def unexpected(*args):
        raise AssertionError("No precision exception is permitted for a material mismatch")
    monkeypatch.setattr(mod, "execute_query", unexpected)
    check = mod.compare_with_raw_precision_check("legacy", dune, hs, ["day"], ["daily_net"], Decimal("0.000001"),
                raw_sql="oracle.sql", raw_params={}, pin_block=100, decimals=6)
    assert not check["matched"]
    assert "legacy_float_comparison" not in check
