"""Historical amounts, booking-once controls and the production MSC bridge."""

import json
import os
import socket
from decimal import Decimal as D
from unittest.mock import patch

import pytest

from scripts.build_jan_sep_2026_reconciliation import (
    calculate,
    forum_post,
)
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
    assert D(combined["grove"]["sv_adj"]) == D("915857.20") + D("11554.74")


def test_bridge_cutoff_and_small_buidl_correction_sign(ledger):
    items = {r["id"]: r for r in ledger["items"]}
    bridge = items["spark-native-usds-transit"]
    assert D(bridge["amount"]) == D("536094.04")
    assert set(bridge["months"]) == {"2026-06", "2026-07", "2026-08", "2026-09"}
    assert D(items["grove-small-buidl-capital-and-fees"]["amount"]) == D("4000.18")
    assert sum(D(r["send_credit"]) for r in ledger["osero_executed_refund_rows"]) == 5514
    # October transit and rebates cannot leak into the historical forum post.
    post = forum_post(ledger)
    assert post.startswith("> **Draft — not ready for booking:")
    assert "correction in PR #281" in post
    assert D(ledger["adjustment_totals"]["spark"]["sv_adj"]) == D("249141.52")
    assert "75,063.88 October bridge" in post


def test_exact_lending_replay_replaces_overlap_and_nets_osero_payment(ledger):
    items = {r["id"]: r for r in ledger["items"]}
    assert ledger["lending_allocation_months"] == 139
    assert "spark-s70" not in items
    assert D(items["spark-lending-event-boundaries"]["amount"]) == D("-1086698.10")
    assert D(items["grove-lending-event-boundaries"]["amount"]) == D("283.23")
    assert all(D(r["net"]) == 0 for r in ledger["prior_payment_netting"])
    netting = ledger["osero_lending_period_netting"]
    assert sum(D(r["income_delta"]) for r in netting) == D("270.63842337876821683000")
    assert all(D(r["corrected_supply_share"]) < 0 for r in netting)
    assert all(D(r["mint_delta"]) == 0 for r in netting)
    assert sum(D(r["transfer_delta"]) for r in netting) == 5514
    assert not any(r.startswith("osero-lending-") for r in items)


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


def test_pr280_replays_only_active_grove_and_preserves_other_primes(ledger):
    from pathlib import Path

    prior = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "reconciliation/2026-01_to_2026-09/before-pr280.json"
        ).read_text()
    )
    assert ledger["grove_pr280_replay"]["active_venue_months"] == 17
    for prime in ["osero", "obex"]:
        assert ledger["adjustment_totals"][prime] == prior["adjustment_totals"][prime]
        assert (
            ledger["linear_settlement_effects"][prime] == prior["linear_settlement_effects"][prime]
        )
    assert D(ledger["adjustment_totals"]["grove"]["sv_adj"]) == D("11554.74")


def test_signed_bridge_replaces_credit_and_excludes_october(ledger):
    assert D(ledger["adjustment_totals"]["spark"]["sky_adj"]) == D("536094.04")
    assert all(r["date"] < "2026-10-01" for r in ledger["bridge_daily"])
    assert sum(D(r["sky_adj"]) for r in ledger["bridge_daily"]).quantize(D(".01")) == D("536094.04")
    assert D(ledger["proposed_combined_october_adjustments"]["spark"]["sky_adj"]) == D("536094.04")
