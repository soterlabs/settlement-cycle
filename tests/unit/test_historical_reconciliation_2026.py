"""Historical amounts, booking-once controls and the production MSC bridge."""

import json
import os
import socket
from decimal import Decimal as D
from unittest.mock import patch

import pytest

from scripts.build_jan_sep_2026_reconciliation import calculate, forum_post
from settle.compute.sky_total_accrual import compute_sky_total_accrual
from settle.domain import Month


@pytest.fixture(scope="module")
def ledger():
    def denied(*args, **kwargs):
        raise AssertionError("Historical component replay attempted network access")

    with (
        patch.dict(os.environ, {"SETTLE_NO_CACHE": "1"}),
        patch.object(socket.socket, "connect", denied),
    ):
        return calculate()


def test_approved_credits_are_not_added_twice_and_october_receipts_are_preserved(ledger):
    remaining = ledger["remaining_to_add_to_october"]
    assert D(remaining["osero"]["send_credit"]) == 0
    assert D(remaining["grove"]["sky_adj"]) == D("-31889.50")
    combined = ledger["proposed_combined_october_adjustments"]
    assert D(combined["osero"]["send_credit"]) == 5514
    assert D(combined["grove"]["sky_adj"]) == D("1141145.80") - D("110419.56")
    assert D(combined["grove"]["sv_adj"]) == D("915857.20") + D("8249.94")


def test_bridge_cutoff_and_small_buidl_correction_sign(ledger):
    items = {r["id"]: r for r in ledger["items"]}
    bridge = items["spark-native-usds-transit"]
    assert D(bridge["amount"]) == D("-255869.48")
    assert set(bridge["months"]) == {"2026-06", "2026-07", "2026-08", "2026-09"}
    assert D(items["grove-small-buidl-capital-and-fees"]["amount"]) == D("4000.18")
    assert sum(D(r["send_credit"]) for r in ledger["osero_executed_refund_rows"]) == 5514
    # October transit and rebates cannot leak into the historical forum post.
    post = forum_post(ledger)
    assert D(ledger["adjustment_totals"]["spark"]["sv_adj"]) == D("1338239.62")
    assert "75,063.88 October bridge" in post


def test_adjustment_effects_match_production_mint_send_core(tmp_path, ledger):
    agents = list(ledger["adjustment_totals"])
    for p in [*agents, "non_msc"]:
        folder = tmp_path / "settlements" / p / "2026-10"
        folder.mkdir(parents=True)
        if p == "non_msc":
            payload = {"total_income": "0", "total_expense": "0"}
        else:
            # An explicitly synthetic positive-SV scenario for the linear
            # table; Osero also exercises the financed-shortfall floor.
            payload = {
                "sky_revenue": "10000000",
                "prime_agent_revenue": "9995000" if p == "osero" else "11000000",
                "agent_rate": "1000000",
                "distribution_rewards": "0",
                "sde_revenue": "0",
            }
        (folder / "provenance.json").write_text(json.dumps({"results": payload}))
    config = {
        "accrual_primes": agents,
        "allocator_ilks": dict.fromkeys(agents, "test"),
        "msc_preview": {"2026-10": {}},
    }
    baseline = compute_sky_total_accrual(Month(2026, 10), repo_root=tmp_path, config=config)
    config["msc_preview"]["2026-10"] = ledger["adjustment_totals"]
    corrected = compute_sky_total_accrual(Month(2026, 10), repo_root=tmp_path, config=config)
    for before, after in zip(baseline.rows, corrected.rows, strict=True):
        expected = ledger["linear_settlement_effects"][before.prime]
        assert after.derived_mint - before.derived_mint == D(expected["mint_delta"])
        assert after.derived_send - before.derived_send == D(expected["transfer_delta"])
        assert (after.derived_mint - after.derived_send) - (
            before.derived_mint - before.derived_send
        ) == D(expected["sky_net_delta"])
