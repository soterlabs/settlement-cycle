"""Offline signed idle/bridge reconciliation; never book or rewrite settlements."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import socket
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from scripts.audit_usds_bridge_transit_2026 import (
    cost_delta,
    frozen_daily_row,
    verified_wallet_usds,
)
from settle.domain import Chain, Period
from settle.domain.config import load_prime_by_id
from settle.extract.hypersync import LogRow
from settle.normalize.usds_bridge_transit import collect_transfers, daily_exposure

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "reconciliation/usds-bridge-2026/baseline-correction"
BASELINE = "536f1492089ed82fbf6eaabb956f7a194b887d47"
PR277 = "ec89f0bdb501d66365979470df0600e820f41a16"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cents(amount):
    return amount.quantize(D(".01"), rounding=ROUND_HALF_UP)


def idle_deductions(path):
    from openpyxl import load_workbook

    book = load_workbook(path, read_only=True, data_only=True)
    try:
        header, values = None, {}
        for row in book["Debt"].values:
            if row[0] == "Date":
                header = list(row)
            elif header and isinstance(row[0], str) and row[0].startswith("2026-"):
                if row[0] in values:
                    raise ValueError("Duplicate workbook date")
                values[row[0]] = D(str(row[header.index("\u2212 ALM idle")]))
        if not values:
            raise ValueError("Missing frozen idle deductions")
        return values
    finally:
        book.close()


def replay(folder=FOLDER):
    def denied(*args, **kwargs):
        raise AssertionError("Offline bridge reconciliation attempted network access")

    with patch.object(socket.socket, "connect", denied):
        return _replay(folder)


def _replay(folder):
    legacy_path = ROOT / "reconciliation/usds-bridge-2026/audit.json"
    legacy = read(legacy_path)
    prime = load_prime_by_id("spark")
    wallets = read(folder / "wallet-inputs.json")
    provenance = read(folder / "sources.json")
    if provenance["baseline_commit"] != BASELINE or provenance["pr277_commit"] != PR277:
        raise ValueError("Wrong frozen baseline or PR #277 checkpoint")
    for path, expected in provenance["sha256"].items():
        if sha(ROOT / path) != expected:
            raise ValueError("Changed retained bridge source: " + path)
    for check in provenance["prior_idle_baseline_checks"]:
        if idle_deductions(ROOT / check["original_file"]) != idle_deductions(
            ROOT / check["frozen_file"]
        ):
            raise ValueError("Idle deductions changed since the prior published baseline")
    checkpoint = read(folder / "pr277-checkpoint.json")
    if checkpoint["baseline_commit"] != BASELINE:
        raise ValueError("PR #277 uses a different baseline")
    old_items = [r for r in checkpoint["items"] if r["id"] == "spark-native-usds-transit"]
    if (
        len(old_items) != 1
        or old_items[0]["prime"] != "spark"
        or old_items[0]["field"] != "sky_adj"
    ):
        raise ValueError("Expected one Spark bridge replacement item")
    old_item = old_items[0]
    old_amount = D(old_item["amount"])
    if cents(D(legacy["primes"]["spark"]["measured_excess_charge"])) != -old_amount:
        # The legacy aggregate includes October: check the historical daily rows instead.
        old_historical = sum(
            (
                D(r["measured_excess_charge"])
                for r in legacy["primes"]["spark"]["daily"]
                if r["date"] <= "2026-09-30"
            ),
            D(0),
        )
        if cents(old_historical) != -old_amount:
            raise ValueError("Legacy historical credit differs from #277")

    raw = read(ROOT / "tests/fixtures/usds_bridge/spark_2026_gateway_logs.json")

    def fetch(chain, selections, start, end, **kwargs):
        return [
            LogRow(**r)
            for r in raw[chain]
            if start <= r["block_number"] <= end
            and any(
                r["address"] in s["address"] and r["topic0"] in s["topics"][0] for s in selections
            )
        ]

    period = Period(
        date(2026, 1, 1),
        date(2026, 10, 31),
        {Chain(c): r["block"] for c, r in wallets["days"]["2026-10-31"].items()},
    )
    transfers = collect_transfers(prime, period, fetch=fetch)
    # November deliveries are intentionally unseen at the October cutoff.
    expected = []
    for item in legacy["transfers"]:
        if item["prime"] != "spark":
            continue
        item = {k: v for k, v in item.items() if k != "prime"}
        if item["received_at"] is not None and item["received_at"] >= 1793491200:
            for key in [
                "received_at",
                "destination_transaction",
                "destination_log_index",
                "destination_block",
            ]:
                item[key] = None
        expected.append(item)
    if transfers != expected:
        raise ValueError("Gateway replay differs from retained bridge claims")

    retained = read(ROOT / "reconciliation/usds-bridge-2026/spark-october-retained-daily.json")
    october = {r["date"]: r for r in retained["daily"]}
    books, daily = {}, []
    months = defaultdict(D)
    for r in daily_exposure(period, transfers).to_dict("records"):
        amount, day = r["cum_balance"], str(r["block_date"])
        if not amount:
            continue  # Only bridge-active dates; do not introduce unrelated earlier idle true-ups.
        month = day[:7]
        row = frozen_daily_row("spark", day, books, october)
        if row is None:
            raise ValueError("Missing frozen baseline on " + day)
        wallet = verified_wallet_usds(wallets, prime, day)
        old_idle = D(str(row.get("\u2212 ALM idle", row.get("alm_usds"))))
        old_bridge = D(str(row.get("bridge_usds", "0")))
        signed_credit = cost_delta(row, amount, prime.subsidy.cap_usd, wallet_usds=wallet)
        if signed_credit is None:
            raise ValueError("Unresolved frozen rate on " + day)
        months[month] += signed_credit
        daily.append(
            {
                "date": day,
                "frozen_idle": str(old_idle),
                "frozen_bridge": str(old_bridge),
                "verified_wallet_usds": str(wallet),
                "eligible_bridge_usds": str(amount),
                "corrected_idle_plus_bridge": str(wallet + amount),
                "deduction_change": str(wallet + amount - old_idle - old_bridge),
                "original_charge": str(row["daily Sky charge"]),
                "corrected_charge": str(D(str(row["daily Sky charge"])) - signed_credit),
                "signed_credit": str(signed_credit),
            }
        )
    if len(daily) != 132:
        raise ValueError("Expected every June 22-October 31 bridge-active close")
    historical_credit = sum((v for m, v in months.items() if m <= "2026-09"), D(0))
    replacement = -cents(historical_credit)
    delta = replacement - old_amount
    combined = copy.deepcopy(checkpoint["adjustment_totals"])
    combined["spark"]["sky_adj"] = str(D(combined["spark"]["sky_adj"]) + delta)
    return {
        "status": "calculated_unbooked_signed_component",
        "baseline_commit": BASELINE,
        "pr277_commit": PR277,
        "scope": "Replace wallet idle plus bridge on bridge-active closes only; all other frozen daily inputs/rates retained. No payment executed.",
        "monthly_signed_credit": {m: str(v) for m, v in sorted(months.items())},
        "historical_signed_credit": str(historical_credit),
        "historical_replacement_sky_adjustment": str(replacement),
        "pr277_superseded_sky_adjustment": str(old_amount),
        "incremental_sky_adjustment_vs_pr277": str(delta),
        "combined_adjustment_totals": combined,
        "october_ordinary_signed_credit": str(months["2026-10"]),
        "prior_netting": provenance["prior_netting"],
        "daily": daily,
        "input_sha256": provenance["sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ["daily", "input_sha256"]}, indent=2
        )
    )


if __name__ == "__main__":
    main()
