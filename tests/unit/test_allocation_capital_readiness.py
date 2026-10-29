"""Operator funding assumption must not charge borrowing costs on earnings."""

import importlib.util
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from settle.normalize.allocation_capital import (
    AssetMovement,
    CapitalBatch,
    CapitalHistory,
    ExternalFundingOperation,
)

spec = importlib.util.spec_from_file_location(
    "readiness",
    Path(__file__).resolve().parents[2] / "scripts/report_allocation_capital_readiness.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def batch(n, movements, *, savings=False):
    return CapitalBatch(
        str(n),
        date(2026, 8, n),
        n,
        "ethereum",
        n,
        tuple(AssetMovement(a, D(v), D(c), D(i)) for a, v, c, i in movements),
        external_funding=(ExternalFundingOperation("draw", "saver", D(100)),) if savings else (),
    )


def timeline(batches):
    return module.capital_timeline(
        CapitalHistory(tuple(batches), {"A": "a", "B": "b"}, {}), date(2026, 8, 1), date(2026, 8, 3)
    )


def test_unknown_principal_assumed_sky_but_appreciation_not_reborrowed():
    result = timeline(
        [
            batch(1, [("a", 0, 100, 0)]),
            batch(2, [("a", 110, -110, 0), ("b", 0, 110, 0)]),
        ]
    )
    assert D(result["2026-08-02"]["b"]["principal"]) == 100
    assert D(result["2026-08-02"]["a"]["principal"]) == 0


def test_gift_then_reinvestment_is_not_principal():
    result = timeline(
        [
            batch(1, [("a", 0, 100, 100)]),
            batch(2, [("a", 100, -100, 0), ("b", 0, 100, 0)]),
        ]
    )
    assert D(result["2026-08-03"]["b"]["principal"]) == 0


def test_partial_sale_and_new_draw_preserve_existing_principal():
    result = timeline(
        [
            batch(1, [("a", 0, 100, 0)]),
            batch(2, [("a", 120, -60, 0), ("b", 0, 100, 0)]),
        ]
    )
    assert D(result["2026-08-03"]["a"]["principal"]) == 50
    # 50 original principal + 40 new funding; 10 realized gain is excluded.
    assert D(result["2026-08-03"]["b"]["principal"]) == 90


def test_savings_dependency_propagates_and_full_exit_clears_it():
    result = timeline(
        [
            batch(1, [("a", 0, 100, 0)], savings=True),
            batch(2, [("a", 100, -100, 0), ("b", 0, 100, 0)]),
            batch(3, [("a", 0, 100, 0)]),
        ]
    )
    assert result["2026-08-02"]["b"]["savings_dependency"]
    assert not result["2026-08-03"]["a"]["savings_dependency"]


def test_partial_exit_does_not_clear_savings_dependency():
    result = timeline([batch(1, [("a", 0, 100, 0)], savings=True), batch(2, [("a", 100, -50, 0)])])
    assert result["2026-08-03"]["a"]["savings_dependency"]


def test_missing_opening_is_not_seeded_from_nav_and_propagates():
    result = timeline([batch(1, [("a", 100, -100, 0), ("b", 0, 100, 0)])])
    assert result["2026-08-01"]["b"]["issues"] == ["missing_opening_capital"]
    assert D(result["2026-08-01"]["b"]["principal"]) == 0


def test_cost_deducts_idle_and_sde_and_preserves_exclusion():
    args = ({"d": D(100)}, {"d": D(".01")}, {"d": D(20)}, {"d": D(30)})
    assert module.price_daily(*args) == D(".50")
    assert module.price_daily(*args, excluded=True) == 0
    with pytest.raises(ValueError, match="Missing daily"):
        module.price_daily({"d": D(100)}, {}, {}, {})


def test_excess_exemption_is_separate_credit_not_negative_borrowing_cost():
    args = ({"d": D(100)}, {"d": D(".01")}, {"d": D(120)}, {})
    cost = module.price_daily(*args)
    uncapped = module.price_daily(*args, cap_deductions=False)
    assert cost == 0
    assert cost - uncapped == D(".20")


def test_duplicate_transaction_rejected():
    b = batch(1, [("a", 0, 100, 0)])
    with pytest.raises(ValueError, match="Duplicate"):
        timeline([b, b])


def test_realized_loss_does_not_fund_new_position():
    result = timeline(
        [batch(1, [("a", 0, 100, 0)]), batch(2, [("a", 80, -80, 0), ("b", 0, 80, 0)])]
    )
    assert D(result["2026-08-03"]["b"]["principal"]) == 80


def test_nft_collect_forwarding_is_not_a_second_principal_inflow():
    result = timeline(
        [
            batch(1, [("a", 0, 100, 0)]),
            batch(
                2,
                [
                    ("a", 100, -10, 0),
                    ("collect", 0, 10, 0),
                    ("collect", 10, -10, 0),
                    ("b", 0, 10, 0),
                ],
            ),
        ]
    )
    assert D(result["2026-08-02"]["a"]["principal"]) == 90
    assert D(result["2026-08-02"]["b"]["principal"]) == 10


def test_discontinuous_repeated_account_is_rejected():
    with pytest.raises(ValueError, match="Discontinuous"):
        timeline([batch(1, [("a", 0, 10, 0), ("a", 11, -10, 0)])])


@pytest.mark.parametrize("savings", [False, True])
def test_report_reuses_revenue_but_withholds_savings_cost(monkeypatch, savings):
    cfg = SimpleNamespace(
        id="A",
        label="Allocation",
        cof_excluded=False,
        sky_direct=False,
        curve_idle_usds=None,
        lending_idle_usds=False,
    )
    monkeypatch.setattr(module, "load_prime_by_id", lambda _: SimpleNamespace(venues=[cfg]))
    history = CapitalHistory((batch(1, [("a", 0, 100, 0)], savings=savings),), {"A": "a"}, {})
    control = {
        "prime_id": "spark",
        "period": {"start": "2026-08-01", "end": "2026-08-01"},
        "sky_revenue_daily": [{"date": "2026-08-01", "daily_sky_rev": "1", "utilized": "100"}],
        "venue_breakdown": [{"venue_id": "A", "revenue": "7"}],
    }
    row = module.report(
        "spark", history, control, {"daily": {}}, date(2026, 8, 1), date(2026, 8, 1)
    )["allocations"][0]
    assert row["revenue"] == "7"
    assert D(row["average_principal"]) == 100
    if savings:
        assert row["status"] == "savings_split_pending"
        assert row["cost_of_funds"] is None
        assert row["net_pnl"] is None
    else:
        assert D(row["cost_of_funds"]) == 1
        assert D(row["net_pnl"]) == 6


@pytest.mark.parametrize("venue", list(module.GROVE_EXTRA_LABELS))
def test_grove_tracking_account_needs_no_revenue_row_to_compute_cost(monkeypatch, venue):
    monkeypatch.setattr(module, "load_prime_by_id", lambda _: SimpleNamespace(venues=[]))
    history = CapitalHistory(
        (batch(1, [("a", 0, 100, 0)]),), {venue: "a"}, {}, analytics_only_venues=(venue,)
    )
    control = {
        "prime_id": "grove",
        "period": {"start": "2026-08-01", "end": "2026-08-01"},
        "sky_revenue_daily": [{"date": "2026-08-01", "daily_sky_rev": "1", "utilized": "100"}],
        "venue_breakdown": [],
    }
    row = module.report(
        "grove", history, control, {"daily": {}}, date(2026, 8, 1), date(2026, 8, 1)
    )["allocations"][0]
    assert row["status"] == "calculable_under_assumption"
    assert D(row["cost_of_funds"]) == 1
    assert row["revenue"] is None
    assert row["net_pnl"] is None


def test_basin_exemption_is_not_backdated_or_silently_omitted_after_september():
    assert module.known_extra_policy("grove", "E_JTRSY_BASIN", date(2026, 8, 31))
    assert not module.known_extra_policy("grove", "E_JTRSY_BASIN", date(2026, 9, 1))
    assert not module.known_extra_policy("grove", "unknown", date(2026, 8, 31))
    assert not module.known_extra_policy("spark", "E_PAXOS_BRIDGE", date(2026, 8, 31))


@pytest.mark.parametrize("bad", ["-1", "NaN", "Infinity"])
@pytest.mark.parametrize("field", ["principal", "rate", "idle", "sde"])
def test_invalid_daily_cost_inputs_are_rejected(field, bad):
    args = {
        "principal": {"d": D(100)},
        "rate": {"d": D(".01")},
        "idle": {"d": D(0)},
        "sde": {"d": D(0)},
    }
    args[field]["d"] = D(bad)
    with pytest.raises(ValueError, match="Invalid daily"):
        module.price_daily(args["principal"], args["rate"], args["idle"], args["sde"])


def test_report_rejects_idle_values_that_do_not_reconcile():
    control = {
        "prime_id": "grove",
        "period": {"start": "2026-08-01", "end": "2026-08-01"},
        "sky_revenue_daily": [
            {"date": "2026-08-01", "daily_sky_rev": "1", "utilized": "100", "curve_idle": "20"}
        ],
        "venue_breakdown": [{"venue_id": "A", "revenue": "7"}],
    }
    with pytest.raises(ValueError, match="idle deductions do not reproduce"):
        module.report(
            "grove",
            CapitalHistory((), {}, {}),
            control,
            {"daily": {"A": {"2026-08-01": "19"}}},
            date(2026, 8, 1),
            date(2026, 8, 1),
        )
    control["sky_revenue_daily"] *= 2
    with pytest.raises(ValueError, match="exactly one row"):
        module.report(
            "grove",
            CapitalHistory((), {}, {}),
            control,
            {"daily": {}},
            date(2026, 8, 1),
            date(2026, 8, 1),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"prime": "grove"},
        {"end": "2026-08-31"},
        {"pins": {"ethereum": 99}},
        {"history_sha256": "stale"},
    ],
)
def test_rejects_history_for_wrong_prime_month_or_blocks(change):
    manifest = {
        "prime": "spark",
        "end": "2026-09-30",
        "pins": {"ethereum": 100},
        "history_sha256": "valid",
    }
    control = {"pin_blocks_eom": {"ethereum": 100}}
    module.validate_history_manifest(
        manifest, prime="spark", end=date(2026, 9, 30), control=control, history_sha256="valid"
    )
    with pytest.raises(ValueError, match="settlement pins"):
        module.validate_history_manifest(
            {**manifest, **change},
            prime="spark",
            end=date(2026, 9, 30),
            control=control,
            history_sha256="valid",
        )


def test_fresh_capture_lineage_includes_transitive_movement_adapters():
    paths = {str(p) for p in module.source_files(with_adapters=True)}
    assert "src/settle/compute/executed_spell_capital.py" in paths
    assert "src/settle/compute/grove_cash_distributions.py" in paths
    assert "src/settle/compute/spark_separate_savings_routes.py" in paths
