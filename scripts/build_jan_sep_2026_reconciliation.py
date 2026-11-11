"""Build incremental Jan-Sep corrections and a forum draft from retained inputs.

Read-only component replay. No monthly run, database publication, settlement
configuration edit, or execution. The output directory must be outside frozen
settlements. Existing approved October amounts are included once, not added twice.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import socket
import sys
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from audit_grove_september_2026 import EVIDENCE  # noqa: E402
from audit_grove_september_2026 import audit as grove_audit  # noqa: E402
from audit_usds_bridge_transit_2026 import cost_delta, historical_rows  # noqa: E402
from reconcile_usds_bridge_baseline_2026 import replay as bridge_baseline_replay  # noqa: E402
from reconcile_async_deposits import reconcile_deposits  # noqa: E402
from reconcile_grove_buidl_fees import reconcile as buidl_reconcile  # noqa: E402
from reconcile_osero_shortfalls import reconcile as osero_reconcile  # noqa: E402
from reconcile_sde_inflight import reconcile as redemption_reconcile  # noqa: E402
from replay_reconciliation_venues import replay as venue_replay  # noqa: E402

from settle.domain import Chain, Period  # noqa: E402
from settle.domain.config import load_prime_by_id  # noqa: E402
from settle.extract.hypersync import LogRow  # noqa: E402
from settle.normalize.usds_bridge_transit import collect_transfers, daily_exposure  # noqa: E402

BASELINE = "536f1492089ed82fbf6eaabb956f7a194b887d47"
CALCULATION = "bda6858"  # Resolved and checked against the inventory's full commit.
FOLDER = ROOT / "reconciliation/2026-01_to_2026-09"
AGENTS = ("spark", "grove", "osero", "obex")
FIELDS = ("dv_adj", "sv_adj", "sky_adj", "send_credit")


def cents(value):
    return D(value).quantize(D(".01"), rounding=ROUND_HALF_UP)


def money(value):
    return f"{D(value):,.2f}"


def calculate_previous_components():
    hashes = {}

    def read(path):
        path = Path(path)
        hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return json.loads(
            gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_text()
        )

    evidence = read(FOLDER / "venue-evidence.json.gz")
    venues = venue_replay(evidence)
    grove = grove_audit(read(EVIDENCE))
    redemption, deposit = redemption_reconcile(), reconcile_deposits()
    buidl, osero = buidl_reconcile(), osero_reconcile()
    async_data = read(ROOT / "reconciliation/async-allocations-2026-01_to_2026-09/results.json")
    for name, digest in async_data["implementation_files_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Async calculation implementation changed: " + name)
    receipts = read(FOLDER / "rlusd-receipts.json")
    dr = read(FOLDER / "dr-code-evidence.json")
    if dr["rows"] or any(r["month"][:7] <= "2026-09" for r in dr["matching_csv_rows"]):
        raise ValueError("Historical Osero DR source needs a nonzero replay")
    for name in [
        "config/sky_total.yaml",
        "config/grove.yaml",
        "config/spark.yaml",
        "config/osero.yaml",
        "tests/fixtures/sde_inflight/centrifuge_history.json",
        "tests/fixtures/sde_inflight/async_deposits.json",
        "tests/fixtures/sde_inflight/buidl_events.json",
        "reconciliation/2026-01_to_2026-08/data.json",
        "reconciliation/2026-10/osero_shortfall_evidence.json",
        "scripts/reconcile_sde_inflight.py",
        "scripts/reconcile_async_deposits.py",
        "scripts/reconcile_grove_buidl_fees.py",
        "scripts/reconcile_osero_shortfalls.py",
        "scripts/audit_grove_september_2026.py",
        "scripts/audit_usds_bridge_transit_2026.py",
        "scripts/replay_reconciliation_venues.py",
        "scripts/build_jan_sep_2026_reconciliation.py",
    ]:
        hashes[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    for record in redemption["months"].values():
        hashes[record["path"]] = record["sha256"]
    items = []

    def item(identity, prime, field, raw, months, source, **extra):
        if field not in FIELDS or any(not "2026-01" <= m <= "2026-09" for m in months):
            raise ValueError("Adjustment outside historical scope")
        if abs(sum((D(v) for v in months.values()), D(0)) - D(raw)) > D(".00000001"):
            raise ValueError("Monthly breakdown does not reproduce " + identity)
        items.append(
            dict(
                id=identity,
                prime=prime,
                field=field,
                unrounded=str(raw),
                amount=str(cents(raw)),
                months={m: str(v) for m, v in months.items()},
                source=source,
                **extra,
            )
        )

    # Recompute authenticated transit exposure with no post-September releases
    # visible. Daily original charges and original rate conventions are reused.
    bridge = read(ROOT / "reconciliation/usds-bridge-2026/audit.json")
    for name, digest in bridge["primes"]["spark"]["baseline_workbook_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Bridge historical workbook changed")
        hashes[name] = digest
    transfers = []
    cutoff = 1790812799
    for t in bridge["transfers"]:
        if t["prime"] != "spark" or t["sent_at"] > cutoff:
            continue
        t = dict(t)
        if t["received_at"] is not None and t["received_at"] > cutoff:
            for key in [
                "received_at",
                "destination_transaction",
                "destination_block",
                "destination_log_index",
            ]:
                t[key] = None
        transfers.append({k: v for k, v in t.items() if k != "prime"})
    pins = {
        Chain(c): b
        for c, b in evidence["baseline_pin_sources"]["spark"]["period"]["pin_blocks"].items()
    }
    period = Period(date(2026, 1, 1), date(2026, 9, 30), pins)
    raw_gateway = read(ROOT / "tests/fixtures/usds_bridge/spark_2026_gateway_logs.json")

    def gateway_reader(chain, selections, start, end, **kwargs):
        return [
            LogRow(**r)
            for r in raw_gateway[chain]
            if start <= r["block_number"] <= end
            and r["block_time"] <= cutoff
            and any(
                r["address"] in sel["address"] and r["topic0"] in sel["topics"][0]
                for sel in selections
            )
        ]

    authenticated = collect_transfers(load_prime_by_id("spark"), period, fetch=gateway_reader)

    def order(t):
        return (t["source_chain"], t["source_block"], t["source_log_index"])

    if sorted(authenticated, key=order) != sorted(transfers, key=order):
        raise ValueError("Bridge claims differ from authenticated gateway source replay")
    transfers = authenticated
    daily = daily_exposure(period, transfers)
    months = defaultdict(D)
    books = {}
    bridge_days = []
    prime = load_prime_by_id("spark")
    for r in daily.to_dict("records"):
        if not r["cum_balance"]:
            continue
        day = str(r["block_date"])
        month = day[:7]
        if month not in books:
            books[month] = historical_rows("spark", month)
        delta = cost_delta(books[month][day], r["cum_balance"], prime.subsidy.cap_usd)
        if delta is None:
            raise ValueError("Unresolved historical bridge rate " + day)
        months[month] -= delta
        bridge_days.append(
            dict(date=day, in_transit_usds=str(r["cum_balance"]), sky_adj=str(-delta))
        )
    item(
        "spark-native-usds-transit",
        "spark",
        "sky_adj",
        sum(months.values(), D(0)),
        months,
        "reconciliation/usds-bridge-2026/audit.json",
        prior_trueup="0",
    )
    # Full payer/recipient/token source history proves these six September
    # payments were absent before September; each native receipt is counted once.
    captured = []
    for key, rows in evidence["log_queries"].items():
        query = json.loads(key)
        if (
            query[0] == "ethereum"
            and query[2] == 0
            and "b70e0fad7d0cf4c187365a84e728802ab24ca78b" in key
            and "8292bb45" in key
        ):
            captured = rows
    if captured != receipts:
        raise ValueError("Receipt list differs from complete payer history")
    ids = set()
    cash = D(0)
    for r in receipts:
        identity = r["transaction_hash"], r["log_index"]
        if (
            r["address"] != "0x8292bb45bf1ee4d140127049757c2e0ff06317ed"
            or r["topic1"] != "0x" + "b70e0fad7d0cf4c187365a84e728802ab24ca78b".zfill(64)
            or r["topic2"] != "0x" + prime.alm[Chain.ETHEREUM].hex[2:].zfill(64)
            or int(r["data"], 16) <= 0
        ):
            raise ValueError("Wrong native RLUSD receipt asset/payer/owner/amount")
        if identity in ids or not 1788220800 <= r["block_time"] <= cutoff:
            raise ValueError("Duplicate/out-of-period native RLUSD receipt")
        ids.add(identity)
        cash += D(int(r["data"], 16)) / 10**18
    item(
        "spark-september-partner-receipts",
        "spark",
        "sv_adj",
        cash,
        {"2026-09": cash},
        "reconciliation/2026-01_to_2026-09/rlusd-receipts.json",
        prior_trueup="0",
    )
    for vid in ["S70", "S72"]:
        value = D(venues[f"spark/2026-09/{vid}"]["venue"]["actual_revenue"])
        # S70 also has external spToken receipts, disjoint from native RLUSD.
        value += D(venues[f"spark/2026-09/{vid}"]["venue"]["external_revenue"])
        item(
            "spark-" + vid.lower(),
            "spark",
            "sv_adj",
            value,
            {"2026-09": value},
            "reconciliation/2026-01_to_2026-09/venue-evidence.json.gz",
            prior_trueup="0",
        )
    for name in ["spark", "obex"]:
        monthly = defaultdict(D)
        for r in async_data["results"]:
            if r["prime"] == name:
                if D(r["global_cof_delta"]) != 0 or D(r["prior_async_credit"]) != 0:
                    raise ValueError("Async audit requires a different netting policy")
                monthly[r["month"]] += D(r["net_income_delta"])
        item(
            name + "-async-vaults",
            name,
            "sv_adj",
            sum(monthly.values(), D(0)),
            monthly,
            "reconciliation/async-allocations-2026-01_to_2026-09/results.json",
            prior_trueup="0",
        )
    monthly = {m: -D(r["additional_credit"]) for m, r in redemption["months"].items()}
    item(
        "grove-redemption-transit",
        "grove",
        "sky_adj",
        -D(redemption["additional_credit_unrounded"]),
        monthly,
        "reconciliation/2026-10/sde_inflight_cof.json",
        prior_trueup="2508.55",
        existing_october_amount=str(redemption["sky_adj"]),
    )
    monthly = {m: -D(v) for m, v in deposit["months"].items()}
    item(
        "grove-issued-deposits",
        "grove",
        "sky_adj",
        -D(deposit["additional_credit_unrounded"]),
        monthly,
        "reconciliation/2026-10/async_deposit_cof.json",
        prior_trueup="0",
    )
    monthly = {
        m: D(buidl["capital_corrections"][m]["restored_sky_income"]) - D(r["additional_credit"])
        for m, r in buidl["months"].items()
    }
    item(
        "grove-small-buidl-capital-and-fees",
        "grove",
        "sky_adj",
        D(buidl["net_sky_adjustment_unrounded"]),
        monthly,
        "scripts/reconcile_grove_buidl_fees.py",
        prior_fee_credit="162505.35",
    )
    value = D(grove["vaults"]["E43"]["revenue_existing_method"])
    item(
        "grove-base-steakhouse",
        "grove",
        "sv_adj",
        value,
        {"2026-09": value},
        "docs/grove/evidence/september-2026-venue-audit.json",
        prior_trueup="0",
    )
    monthly = {
        f"2026-{m:02}": D(venues[f"grove/2026-{m:02}/E44"]["venue"]["actual_revenue"])
        for m in [7, 8, 9]
    }
    item(
        "grove-diamond-uniswap",
        "grove",
        "sv_adj",
        sum(monthly.values(), D(0)),
        monthly,
        "reconciliation/2026-01_to_2026-09/venue-evidence.json.gz",
        prior_trueup="0",
    )
    monthly = {r["earning_month"]: D(r["send_credit"]) for r in osero["rows"]}
    item(
        "osero-financed-shortfall-refund",
        "osero",
        "send_credit",
        D(osero["send_credit"]),
        monthly,
        "reconciliation/2026-10/osero_shortfall_evidence.json",
        existing_october_amount=osero["send_credit"],
    )
    totals = {p: {f: D(0) for f in FIELDS} for p in AGENTS}
    for r in items:
        totals[r["prime"]][r["field"]] += D(r["amount"])
    historical_effects = {}
    for p, t in totals.items():
        # Linear effects of adjustment inputs, before the existing monthly SV
        # floor and whole-USDS rounding. Ordinary month values are not invented.
        historical_effects[p] = dict(
            mint_delta=str(t["sky_adj"] + t["sv_adj"]),
            transfer_delta=str(t["dv_adj"] + t["sv_adj"] + t["send_credit"]),
            sky_net_delta=str(t["sky_adj"] - t["dv_adj"] - t["send_credit"]),
        )
    # Existing October configuration mixes ordinary receipts with two historical
    # credits. Strip only those historical credits to obtain the ordinary baseline.
    config = yaml.safe_load((ROOT / "config/sky_total.yaml").read_text())
    existing = config["msc_preview"]["2026-10"]
    ordinary = {p: {f: D(str(existing.get(p, {}).get(f, 0))) for f in FIELDS} for p in AGENTS}
    already = {p: {f: D(0) for f in FIELDS} for p in AGENTS}
    already["grove"]["sky_adj"] = D(redemption["sky_adj"])
    already["osero"]["send_credit"] = D(osero["send_credit"])
    proposed = {}
    remaining = {}
    for p in AGENTS:
        for f in FIELDS:
            ordinary[p][f] -= already[p][f]
        proposed[p] = {f: str(ordinary[p][f] + totals[p][f]) for f in FIELDS}
        remaining[p] = {f: str(totals[p][f] - already[p][f]) for f in FIELDS}
    if ordinary["osero"]["send_credit"] != 0 or ordinary["grove"]["sky_adj"] != D("1141145.80"):
        raise ValueError(
            "Existing approved credits/ordinary receipts changed; review before netting"
        )
    inventory = read(FOLDER / "pr-inventory.json")
    if inventory["baseline_commit"] != BASELINE or not inventory["calculation_commit"].startswith(
        CALCULATION
    ):
        raise ValueError("Wrong PR comparison range")
    return dict(
        baseline_commit=BASELINE,
        calculation_commit=inventory["calculation_commit"],
        earning_window=["2026-01", "2026-09"],
        items=items,
        adjustment_totals={p: {f: str(v) for f, v in t.items()} for p, t in totals.items()},
        linear_settlement_effects=historical_effects,
        already_in_october={p: {f: str(v) for f, v in t.items()} for p, t in already.items()},
        remaining_to_add_to_october=remaining,
        proposed_combined_october_adjustments=proposed,
        bridge_daily=bridge_days,
        osero_executed_refund_rows=osero["rows"],
        input_sha256=hashes,
        rounding="Each booked component is rounded to cents; preserve existing approved components. Monthly detail remains unrounded. Final MSC applies its usual SV floor and whole-USDS rounding.",
        execution_status="Calculated proposal; no new settlement configuration, database revision or on-chain execution.",
    )


def osero_period_effect(row, income_delta):
    """Net corrected income and the financed deficit against executed cashflows."""

    def whole(v):
        return D(v).quantize(D(1), rounding=ROUND_HALF_UP)

    supply = D(row["supply_share"]) + D(income_delta)
    payable = max(supply, D(0))
    mint = whole(D(row["cof"]) + payable)
    send = whole(D(row["demand_revenue"]) + payable)
    mint_delta = mint - D(row["debt_minted"])
    transfer_delta = send - D(row["actual_send"])
    return dict(
        earning_month=row["earning_month"],
        income_delta=str(income_delta),
        corrected_supply_share=str(supply),
        corrected_mint=str(mint),
        corrected_send=str(send),
        mint_delta=str(mint_delta),
        transfer_delta=str(transfer_delta),
        additional_send_credit=str(transfer_delta - mint_delta - D(row["send_credit"])),
    )


def apply_signed_bridge(data):
    """Replace the superseded credit once, keeping October ordinary CoF separate."""
    replayed = bridge_baseline_replay()
    matches = [r for r in data["items"] if r["id"] == "spark-native-usds-transit"]
    if len(matches) != 1:
        raise ValueError("Expected one historical Spark bridge component")
    row = matches[0]
    months = {m: -D(v) for m, v in replayed["monthly_signed_credit"].items()
              if m <= "2026-09"}
    amount = cents(sum(months.values(), D(0)))
    delta = amount - D(row["amount"])
    row.update(amount=str(amount), unrounded=str(sum(months.values(), D(0))),
               months={m: str(v) for m, v in months.items()},
               source="reconciliation/usds-bridge-2026/baseline-correction/results.json")
    for key in ["adjustment_totals", "remaining_to_add_to_october",
                "proposed_combined_october_adjustments"]:
        data[key]["spark"]["sky_adj"] = str(D(data[key]["spark"]["sky_adj"]) + delta)
    for key in ["mint_delta", "sky_net_delta"]:
        data["linear_settlement_effects"]["spark"][key] = str(
            D(data["linear_settlement_effects"]["spark"][key]) + delta)
    data["bridge_daily"] = [dict(r, sky_adj=str(-D(r["signed_credit"])))
                            for r in replayed["daily"] if r["date"] < "2026-10-01"]
    data["input_sha256"].update(replayed["input_sha256"])
    for name in ["scripts/reconcile_usds_bridge_baseline_2026.py",
                 "scripts/build_jan_sep_2026_reconciliation.py",
                 "reconciliation/usds-bridge-2026/baseline-correction/sources.json"]:
        data["input_sha256"][name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    data["execution_status"] = "Grove cash and signed Spark wallet/bridge component replays complete; no production booking or execution."
    return data


def calculate():
    if (FOLDER / "before-pr280.json").exists():
        from update_grove_pr280_reconciliation import update
        return apply_signed_bridge(update(FOLDER))
    capture_path = FOLDER / "atoken-inputs.json.gz"
    replay_path = FOLDER / "atoken-replay.json"
    if not capture_path.exists() or not replay_path.exists():
        raise RuntimeError("Expanded #220 replay requires complete pinned inputs and exact results")
    capture = json.loads(gzip.decompress(capture_path.read_bytes()))
    replay = json.loads(replay_path.read_text())
    if (
        capture["status"] != "event_states_complete"
        or replay["status"] != "production_component_replay_complete"
        or replay["input_sha256"] != hashlib.sha256(capture_path.read_bytes()).hexdigest()
    ):
        raise ValueError("Expanded replay inputs are incomplete or differ from exact results")
    for name, digest in replay["implementation_files_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Lending replay implementation changed: " + name)
    for name, digest in capture["config_sha256"].items():
        if hashlib.sha256((ROOT / "config" / (name + ".yaml")).read_bytes()).hexdigest() != digest:
            raise ValueError("Lending replay configuration changed: " + name)
    identities = {(r["prime"], r["month"], r["venue"]) for r in replay["results"]}
    expected = set()
    for baseline in capture["baselines"]:
        prime = load_prime_by_id(baseline["prime"])
        end = date.fromisoformat(baseline["period"]["end"])
        for v in capture["venues"].values():
            if v["prime"] != prime.id:
                continue
            venue = next(a for a in prime.venues if a.id == v["venue"])
            if not venue.start_date or venue.start_date <= end:
                expected.add((prime.id, baseline["month"], venue.id))
    if identities != expected or len(identities) != 139 or len(replay["results"]) != 139:
        raise ValueError("Expected all 139 lending allocation-months exactly once")
    data = calculate_previous_components()
    ordinary = {
        p: {
            f: D(data["proposed_combined_october_adjustments"][p][f])
            - D(data["adjustment_totals"][p][f])
            for f in FIELDS
        }
        for p in AGENTS
    }
    # The comprehensive lending replay includes S70. Replace its earlier
    # missing-venue component instead of paying it a second time.
    data["items"] = [r for r in data["items"] if r["id"] != "spark-s70"]
    monthly = {p: defaultdict(D) for p in ["spark", "grove", "osero"]}
    for r in replay["results"]:
        if D(r["global_cof_delta"]) != 0:
            raise ValueError("Lending replay unexpectedly changes whole-prime CoF")
        monthly[r["prime"]][r["month"]] += D(r["income_delta"])

    def add(identity, prime, field, months, **extra):
        value = sum(months.values(), D(0))
        data["items"].append(
            dict(
                id=identity,
                prime=prime,
                field=field,
                unrounded=str(value),
                amount=str(cents(value)),
                months={m: str(v) for m, v in months.items()},
                source="reconciliation/2026-01_to_2026-09/atoken-replay.json",
                **extra,
            )
        )

    for prime in ["spark", "grove"]:
        add(
            prime + "-lending-event-boundaries",
            prime,
            "sv_adj",
            monthly[prime],
            prior_trueup="0",
            replaces="spark-s70" if prime == "spark" else None,
        )
    # RF recognition before September remains outside current dated routing.
    # Show its gross historical recognition and prior payment as an equal pair;
    # do not subtract that credit from the unrelated native-yield difference.
    data["prior_payment_netting"] = [
        dict(
            prime="spark",
            item="historical-reserve-factor",
            gross="2392354.07",
            previously_settled="2392354.07",
            net="0.00",
        ),
        dict(
            prime="grove",
            item="historical-buidl-fees-and-cof",
            gross="165013.90",
            previously_settled="165013.90",
            net="0.00",
            note="Fee and CoF offsets are also preserved in their component replays.",
        ),
    ]
    effects = [
        osero_period_effect(r, monthly["osero"][r["earning_month"]])
        for r in data["osero_executed_refund_rows"]
    ]
    data["osero_lending_period_netting"] = effects
    if any(D(r["mint_delta"]) for r in effects):
        add(
            "osero-lending-positive-share",
            "osero",
            "sv_adj",
            {r["earning_month"]: D(r["mint_delta"]) for r in effects},
        )
    if any(D(r["additional_send_credit"]) for r in effects):
        add(
            "osero-lending-payment-rounding",
            "osero",
            "send_credit",
            {r["earning_month"]: D(r["additional_send_credit"]) for r in effects},
        )
    totals = {p: {f: D(0) for f in FIELDS} for p in AGENTS}
    for r in data["items"]:
        totals[r["prime"]][r["field"]] += D(r["amount"])
    data["adjustment_totals"] = {p: {f: str(v) for f, v in t.items()} for p, t in totals.items()}
    data["remaining_to_add_to_october"] = {
        p: {f: str(t[f] - D(data["already_in_october"][p][f])) for f in FIELDS}
        for p, t in totals.items()
    }
    data["proposed_combined_october_adjustments"] = {
        p: {f: str(ordinary[p][f] + t[f]) for f in FIELDS} for p, t in totals.items()
    }
    data["linear_settlement_effects"] = {
        p: dict(
            mint_delta=str(t["sky_adj"] + t["sv_adj"]),
            transfer_delta=str(t["dv_adj"] + t["sv_adj"] + t["send_credit"]),
            sky_net_delta=str(t["sky_adj"] - t["dv_adj"] - t["send_credit"]),
        )
        for p, t in totals.items()
    }
    data["lending_income_deltas"] = {
        p: {m: str(v) for m, v in rows.items()} for p, rows in monthly.items()
    }
    data["scope"] = (
        "Current applicable fixes from PR #220; January-September 2026, net of prior settlements"
    )
    data["lending_allocation_months"] = len(identities)
    for path in [capture_path, replay_path]:
        data["input_sha256"][str(path.relative_to(ROOT))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return data


def forum_post(data):
    totals = data["adjustment_totals"]
    text = f"""# Settlement Reconciliation - MSC #5-#13 (January-September 2026)

Draft for the October 2026 earning cycle (MSC #14, paid in November).
October's ordinary settlement is presented separately.

We replayed the applicable corrections starting with [PR #220](https://github.com/soterlabs/settlement-cycle/pull/220)
through [`{data["calculation_commit"][:7]}`](https://github.com/soterlabs/settlement-cycle/commit/{data["calculation_commit"]}),
against the published January-September reports. The September baseline is
[`536f149`](https://github.com/soterlabs/settlement-cycle/commit/{BASELINE}).
Amounts already handled in the [previous reconciliation](https://forum.skyeco.com/t/settlement-reconciliation-msc-5-12-january-august-2026/28273)
are deducted. We do not sum isolated PR estimates or rebook earlier payments.

## Historical adjustment ledger

All amounts are USDS. Positive supply-side adjustments add income; negative
ones recover overstated income. Negative Sky-share adjustments reduce the
agent's debt mint. A send-only refund increases its transfer without new debt.

| Prime | Supply-side adjustment | Sky-share adjustment | Send-only refund |
|---|---:|---:|---:|
"""
    for p in AGENTS:
        text += (
            f"| {p.title()} | "
            + " | ".join(money(totals[p][f]) for f in ["sv_adj", "sky_adj", "send_credit"])
            + " |\n"
        )
    text += """
Components are rounded to cents individually, preserving previously approved
credits. Monthly detail retains unrounded values. The ordinary MSC applies its
existing supply-side floor and whole-USDS rounding to actual October figures.

## What changed

- **Aave/SparkLend, Spark/Grove/Osero (#220):** replay all 139 allocation-months
  using complete holder event histories and pinned archive balances at actual
  event boundaries. This replaces approximated daily boundaries and the earlier
  event-principal estimate. Current production yield formulas, rounding and
  clean-exit behavior are retained. Whole-prime CoF does not change; venue CoF
  attribution can change with the capital time series.
- **Spark partner receipts (#267):** recognize 1,373,208.00 of native RLUSD
  received in September, plus Sentora RLUSD investment income of 3,674.16.
  SparkLend RLUSD S70 is included once in the comprehensive lending replay,
  replacing its former standalone 2,400.00 correction.
- **Spark native-USDS wallet/bridge correction (#281):** 557,546.90 of
  additional historical debt mint, June-September, replacing the superseded
  255,869.48 transit credit. Compare verified wallets plus eligible transit
  against frozen deductions, preserving original daily charge conventions.
- **Spark/Obex async vaults (#278, implementing #263):** correct queued shares,
  paid cash and dated capital flows. Spark's correction is -41,042.54;
  Obex's July correction is -273,508.90. Global CoF is unchanged.
- **Grove async CoF (#226):** 78,530.06 net redemption credit, after the earlier
  2,508.55 credit, plus 35,889.68 for issued deposits. The deposit calculation
  is marginal to corrected redemptions, avoiding overlapping exemptions.
- **Grove small BUIDL exits (#268):** 4,000.18 Sky-side debit: restore 4,001.00
  of omitted principal, less 0.82 of remaining fee credit. Previously credited
  fees and September's recognized boundary costs are excluded.
- **Grove missing venues (#264):** 7,219.49 September income from Base
  Steakhouse, and 1,030.45 from Diamond PAU Uniswap (27.90 August; 1,002.55
  September). The LP has no USDS leg and creates no extra CoF exemption.
- **Grove Base cash capital and E12 weights (#280):** exact cash adds 13,769.34
  to historical income. Net the inherited June E23 published-boundary difference
  of -10,747.77 once, for +3,021.57 added to the preceding ledger. E43's earlier
  missing-venue credit is preserved once. E12 changes attribution only. This
  update replays only Grove's 17 active Base allocation-months; all other prime
  results remain at their previously verified calculation versions.
- **Osero duplicate shortfall deduction (#270):** compare corrected rounded
  debt mints/transfers against executed cashflows for July-September, combining
  the lending replay with the refund. Income absorbed by a still-negative
  supply-side share does not create a second transfer or refund.

### Exact lending replay

These are revenue changes before Osero's period-net payment treatment.
Spark's column includes the S70 missing-venue correction; it is not added again.

| Earning month | Spark income change | Grove income change | Osero income change |
|---|---:|---:|---:|
"""
    for m in [f"2026-{n:02}" for n in range(1, 10)]:
        text += (
            "| "
            + m
            + " | "
            + " | ".join(
                money(data["lending_income_deltas"][p].get(m, 0))
                for p in ["spark", "grove", "osero"]
            )
            + " |\n"
        )
    text += """
### Osero payment netting

| Earning month | Income change | Corrected supply share | Corrected debt mint | Corrected transfer | Transfer increase vs executed |
|---|---:|---:|---:|---:|---:|
"""
    for r in data["osero_lending_period_netting"]:
        text += (
            "| "
            + r["earning_month"]
            + " | "
            + " | ".join(
                money(r[f])
                for f in [
                    "income_delta",
                    "corrected_supply_share",
                    "corrected_mint",
                    "corrected_send",
                    "transfer_delta",
                ]
            )
            + " |\n"
        )
    text += """
## Prior credits and booking once

Spark's 2,392,354.07 reserve-factor credit and Grove's 165,013.90 BUIDL/CoF
credit were already handled. Their historical gross recognition and prior
payment net to zero. Dated September activation rules remain in place; these
credits are not deducted again from unrelated native-yield corrections.
September venue results already refreshed before publication are the baseline,
including Spark S1's already-recognized external receipts.

Grove's -78,530.06 historical Sky adjustment and Osero's 5,514.00 refund are
already configured for October. The full ledger above includes them once.
The following amounts remain to add to those existing October entries:

| Prime | Additional supply-side adjustment | Additional Sky-share adjustment | Additional send-only refund |
|---|---:|---:|---:|
"""
    for p in AGENTS:
        text += (
            f"| {p.title()} | "
            + " | ".join(
                money(data["remaining_to_add_to_october"][p][f])
                for f in ["sv_adj", "sky_adj", "send_credit"]
            )
            + " |\n"
        )
    text += """
## Effects on Sky and prime agents

The full historical ledger has the following linear effects relative to the
ordinary October settlement, before the final floor and whole-USDS rounding.
Debt-mint effects assume the combined supply-side share is above the mint floor.
Sky net means debt minted less transfers to agents.

| Prime | Debt mint change | Transfer change | Sky net change |
|---|---:|---:|---:|
"""
    for p in AGENTS:
        text += (
            f"| {p.title()} | "
            + " | ".join(
                money(data["linear_settlement_effects"][p][f])
                for f in ["mint_delta", "transfer_delta", "sky_net_delta"]
            )
            + " |\n"
        )
    text += """
## Scope and evidence

The [calculation package](https://github.com/soterlabs/settlement-cycle/pull/277)
contains retained source inputs, hashes, per-venue and monthly results, the PR
inventory, offline replay scripts and proposed **replacement** October values.
Those values preserve ordinary October receipts and do not add another layer
of historical credits. This draft changes no frozen report, production
configuration, database revision or executed payment.

Partner rebates received in October are ordinary October income and excluded
from this historical ledger. Spark's 75,063.88 October bridge CoF effect also
belongs in October's ordinary calculation. Warehouse/Basin presentation and
venue CoF reattribution create no separate whole-prime credit. Basin's September
activation remains dated. Osero referral codes 3014/3017 add no historical DR
on the checked source. The existing zero-effect async findings remain unchanged.

This is a read-only component comparison, not a new complete monthly ALM
transfer-review attestation. Original daily rates, NAVs, subsidy conventions and
applicable policy dates are preserved. The bridge audit covers its four
supported Ethereum routes; unrelated bridge families are not implicitly certified.
"""
    return (
        "> **Draft — not ready for booking:** the signed correction in PR #281 is included. "
        "This read-only component replay does not certify complete monthly asset controls.\n\n"
        + text
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output_dir.resolve()
    if destination.is_relative_to(ROOT / "settlements") or destination.is_relative_to(
        ROOT / "config"
    ):
        parser.error("Use a separate reconciliation output directory")
    os.environ["SETTLE_NO_CACHE"] = "1"

    def no_network(*args, **kwargs):
        raise AssertionError("Offline reconciliation attempted network access")

    with patch.object(socket.socket, "connect", no_network):
        result = calculate()
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "data.json").write_text(json.dumps(result, indent=2) + "\n")
    (destination / "forum-post.md").write_text(forum_post(result))
    (destination / "proposed-october-adjustments.json").write_text(
        json.dumps(
            {
                "status": "Proposed replacement values, not an additional layer of adjustments",
                "ordinary_october_receipts_preserved": True,
                "msc_preview": {"2026-10": result["proposed_combined_october_adjustments"]},
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps(result["adjustment_totals"], indent=2))


if __name__ == "__main__":
    main()
