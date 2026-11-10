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


def forum_post(data):
    totals = data["adjustment_totals"]
    expected = {
        "spark": ("1338239.62", "-255869.48", "0"),
        "grove": ("8249.94", "-110419.56", "0"),
        "osero": ("0", "0", "5514"),
        "obex": ("-273508.90", "0", "0"),
    }
    if any(
        tuple(D(totals[p][f]) for f in ["sv_adj", "sky_adj", "send_credit"])
        != tuple(map(D, values))
        for p, values in expected.items()
    ):
        raise ValueError("Historical totals changed; review the fixed-period forum narrative")
    monthly = {f"2026-{m:02}": {p: {f: D(0) for f in FIELDS} for p in AGENTS} for m in range(1, 10)}
    for r in data["items"]:
        for month, amount in r["months"].items():
            monthly[month][r["prime"]][r["field"]] += D(amount)
    text = f"""# Settlement Reconciliation - MSC #5-#13 (January-September 2026)

Draft prepared for the October 2026 earning cycle (MSC #14, paid in November).
October's ordinary settlement will be presented separately.

This is an incremental reconciliation of corrections merged after the code used
for the last MSC, [`{BASELINE[:7]}`](https://github.com/soterlabs/settlement-cycle/commit/{BASELINE}),
through [`{data["calculation_commit"][:7]}`](https://github.com/soterlabs/settlement-cycle/commit/{data["calculation_commit"]}).
It follows the [January-August reconciliation](https://forum.skyeco.com/t/settlement-reconciliation-msc-5-12-january-august-2026/28273).
The published reports and previously settled adjustments remain the baseline.

## Historical adjustment ledger

All figures are USDS. Positive supply-side adjustments add income; negative
ones recover overstated income. A negative Sky-share adjustment gives the prime
debt-mint relief. Send-only refunds create no additional debt.

| Prime | Supply-side adjustment | Sky-share adjustment | Send-only refund |
|---|---:|---:|---:|
"""
    for p in AGENTS:
        t = totals[p]
        text += f"| {p.title()} | {money(t['sv_adj'])} | {money(t['sky_adj'])} | {money(t['send_credit'])} |\n"
    text += """
Each independently booked component is rounded to cents, preserving the already
approved components. Monthly rows below show unrounded components at display
precision; their sum can differ by a cent from booked totals. The final MSC
applies its existing supply-side floor and whole-USDS rounding.

## Method and prior settlements

The [calculation package](https://github.com/soterlabs/settlement-cycle/pull/277)
contains the PR inventory, immutable input hashes, monthly component ledger,
transaction evidence, offline replay and proposed October adjustment values.
This is a component replay using existing revenue, ownership and borrowing-charge
mechanisms. Original dates, NAVs, subsidy caps and daily rate conventions are
preserved. Historical workbooks are not overwritten.

Earlier adjustments remain excluded: Spark's 2,392,354.07 reserve-factor credit
and Grove's 165,013.90 BUIDL/CoF credit were handled in the previous post. The
2,508.55 August in-flight credit within Grove's earlier total is explicitly
subtracted from the new redemption calculation. Previously credited BUIDL fees
are also subtracted before computing the small remaining correction.

## Spark

- **Native-USDS bridge CoF: 255,869.48 credit**, June-September. Eligible USDS
  remains exempt during authenticated bridge transit when it was exempt on the
  source chain. The replay removes the missing claim from utilized debt using
  the original daily charge convention. It creates debt-mint relief only.
- **September partner receipts: 1,373,208.00 of supply-side income.** Six native
  RLUSD payments reached Spark's ALM. Complete payer/recipient history shows
  no earlier payment in the historical earning window. These are disjoint from
  the spToken receipts included in the lending venue below.
- **September missing investment income: 6,074.16.** Sentora RLUSD S72 adds
  3,674.16; SparkLend RLUSD S70 adds 2,400.00, including 113.73 of reserve
  receipts. The existing event-day NAV and lending-yield conventions are retained.
- **Maple async capital/income: -41,042.54**, January-July. Retaining queued
  shares and using actual paid cash corrects S14/S15's earlier capital/income
  treatment. The replay first reproduces the published venue income. This is
  an income debit, with no change to whole-prime CoF or rate formulas.

Spark's net supply-side adjustment is **1,338,239.62**, separate from its
**255,869.48** Sky-share/debt credit. The RLUSD receipts and investments here
belong to Spark; Grove's idle RLUSD was already tracked in the baseline.

## Grove

- **Redemption CoF: 78,530.06 net credit.** Includes the JAAA settlement-day
  debit, JTRSY/BUIDL corrections and deduction of the earlier 2,508.55 credit.
  This historical amount is already configured for October and is counted once.
- **Issued-deposit CoF: 35,889.68 additional credit**, January/March/April.
  Issued but uncollected JTRSY shares retain their applicable SDE treatment.
  The calculation is marginal to corrected redemptions, so shared dates and
  subsidy-tranche crossings do not receive a second deduction.
- **Small BUIDL capital/fees: 4,000.18 debit.** Restore 4,001.00 of share
  principal previously omitted by the small-transfer filter, less 0.82 of
  additional cash-cost credit after subtracting previously credited fees.
  September's already-recognized boundary fee and transition markdown are excluded.
- **Base Steakhouse vault E43: 7,219.49 of September income.** Complete owner
  share history starts in September; there is no earlier 2026 holding to true up.
- **Diamond PAU Uniswap E44: 1,030.45 of income**, comprising August 27.90 and
  September 1,002.55. The position is additional to the legacy ALM's position.
  The pool has no USDS leg, so adding its income creates no new CoF exemption.

The booked total is **110,419.56 of Sky-share/debt relief** and **8,249.94 of
additional supply-side income**. Warehouse principal and Basin presentation
changes redistribute venue CoF/reporting but add no separate headline credit.

## Osero

Osero's full borrowing cost was already financed through its allocator debt.
The historical deficit also reduced its demand-side transfer, charging the gap
a second time. Refund only that second deduction through `send_credit`.

| Earning month | Executed transfer | Correct transfer | Refund |
|---|---:|---:|---:|
"""
    for r in data["osero_executed_refund_rows"]:
        text += f"| {r['earning_month']} | {money(r['actual_send'])} | {money(r['corrected_send'])} | {money(r['send_credit'])} |\n"
    text += """| **Total** | | | **5,514.00** |

These are differences between actual rounded transfers, not a fresh rounding
of the three deficits. The refund is already configured in October and must
not be added again. It increases the transfer only, with **zero debt-mint increase**.

## Obex

The shared async follow-up identifies **273,508.90 of overstated July income**.
Without queue routing, withdrawals had been replaced by a synthetic month-end
capital outflow. Actual dated queue movements and paid cash reduce that income.
Apply **-273,508.90 as a supply-side adjustment**. Whole-prime CoF is unchanged;
changes in venue attribution are reporting effects, not another credit.

## Monthly historical components

| Month | Spark SV | Spark Sky share | Grove SV | Grove Sky share | Osero refund | Obex SV |
|---|---:|---:|---:|---:|---:|---:|
"""
    for month, primes in monthly.items():
        values = [
            primes["spark"]["sv_adj"],
            primes["spark"]["sky_adj"],
            primes["grove"]["sv_adj"],
            primes["grove"]["sky_adj"],
            primes["osero"]["send_credit"],
            primes["obex"]["sv_adj"],
        ]
        text += "| " + month + " | " + " | ".join(money(v) for v in values) + " |\n"
    text += (
        "| **Booked totals** | "
        + " | ".join(
            money(v)
            for v in [
                totals["spark"]["sv_adj"],
                totals["spark"]["sky_adj"],
                totals["grove"]["sv_adj"],
                totals["grove"]["sky_adj"],
                totals["osero"]["send_credit"],
                totals["obex"]["sv_adj"],
            ]
        )
        + " |\n"
    )
    text += """
## Settlement effects and booking once

The full historical ledger has the following linear effects relative to the
ordinary October settlement. Mint effects assume the combined supply-side share
remains above the existing mint floor; the October calculation must apply the
normal floor and final rounding to its actual ordinary figures.

| Prime | Debt mint change | Transfer change | Sky settlement net change |
|---|---:|---:|---:|
"""
    summed = {k: D(0) for k in ["mint_delta", "transfer_delta", "sky_net_delta"]}
    for p in AGENTS:
        r = data["linear_settlement_effects"][p]
        text += f"| {p.title()} | " + " | ".join(money(r[k]) for k in summed) + " |\n"
        for k in summed:
            summed[k] += D(r[k])
    text += "| **Total** | " + " | ".join(money(v) for v in summed.values()) + " |\n"
    text += """
Sky settlement net means debt minted less transfers to agents. A supply-side
income correction changes mint and send equally in the positive-share case.
CoF relief uses `sky_adj`; it is not credited again through `sv_adj`. Osero's
send-only refund reduces Sky's settlement net without issuing a second loan.

Because Grove's 78,530.06 credit and Osero's 5,514.00 refund are already configured,
the **remaining additions to the existing October entries** are:

| Prime | Additional SV adjustment | Additional Sky-share adjustment | Additional send refund |
|---|---:|---:|---:|
"""
    for p in AGENTS:
        r = data["remaining_to_add_to_october"][p]
        text += f"| {p.title()} | {money(r['sv_adj'])} | {money(r['sky_adj'])} | {money(r['send_credit'])} |\n"
    text += """
The calculation package includes combined October values that preserve ordinary
October receipts. It does not claim the configured historical credits have been
executed, and this draft does not itself change settlement configuration.

## Exclusions and zero effects

- Partner rebates received in October are ordinary October income, even where
  their commercial reference period begins earlier. They are outside this post.
- Spark's 75,063.88 October bridge CoF effect belongs in October's ordinary
  calculation. It is excluded from the January-September credit.
- Spark Ethena, BUIDL, USTB and USCC add no incremental January-September income
  correction. Complete earlier histories establish paid opening claims and no
  new 2026 async activity. Ordinary wallet yield remains included. The existing
  Spark JTRSY adapter is retained; Arkis's observed withdrawals settle atomically.
- Osero referral-code mappings 3014/3017 add zero January-September DR on the
  checked upstream source. No later receipts or unrelated source revisions are imported.
- The native-USDS bridge scan covers the four supported Ethereum routes.
  Grove's separate USDC returns do not establish a native-USDS credit; other
  bridge families are not implicitly certified by this calculation.

## Cause index

| PR | Historical correction |
|---|---|
| [#226](https://github.com/soterlabs/settlement-cycle/pull/226) | Grove async redemption and issued-deposit CoF |
| [#264](https://github.com/soterlabs/settlement-cycle/pull/264) | Grove Base vault and Diamond LP income |
| [#268](https://github.com/soterlabs/settlement-cycle/pull/268) | Grove small BUIDL capital/cash costs; warehouse/Basin reporting |
| [#267](https://github.com/soterlabs/settlement-cycle/pull/267) | Spark RLUSD receipts and investments |
| [#270](https://github.com/soterlabs/settlement-cycle/pull/270) | Osero executed duplicate-shortfall refund |
| [#274](https://github.com/soterlabs/settlement-cycle/pull/274) | Spark native-USDS bridge CoF |
| [#278](https://github.com/soterlabs/settlement-cycle/pull/278) | Spark/Obex Maple income; shared async ownership/capital follow-up |

The [PR inventory and calculation ledger](https://github.com/soterlabs/settlement-cycle/pull/277)
classify every merged PR in the comparison range, including documentation,
reporting-only changes and zero-amount referral-code changes. This post proposes
only the incremental historical corrections above; it does not restate rates
or claim to be a new complete monthly ALM transfer-review attestation.
"""
    return text


def calculate():
    raise RuntimeError(
        "Expanded #220 replay is in progress. Previous-scope totals are provisional; "
        "complete the pinned source replay and prior-payment netting before generating a new proposal."
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
