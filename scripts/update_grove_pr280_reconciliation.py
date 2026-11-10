"""Replay only Grove's active historical Base/LP positions after PR #280.

Other primes and previously verified Grove components are immutable checkpoints.
No monthly settlement, network request, publication or payment is performed.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from pathlib import Path

from reconcile_grove_base_cash import replay

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_SHA256 = "349963c716a89e04db54ba3159835b5b4a9ec893adfc71672bc8959148017b3c"
FIELDS = ("dv_adj", "sv_adj", "sky_adj", "send_credit")
MAIN = "703c66863a0e99edf839658081026926f038c70b"


def update(folder):
    checkpoint = folder / "before-pr280.json"
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != CHECKPOINT_SHA256:
        raise ValueError("Previous reconciliation checkpoint changed")
    saved = json.loads(checkpoint.read_text())
    result = json.loads(checkpoint.read_text())
    replayed = replay(historical_only=True, nonzero_only=True)
    if len(replayed["results"]) != 17:
        raise ValueError("Expected exactly 17 active Grove allocation-months")
    monthly = defaultdict(D)
    for row in replayed["results"]:
        monthly[row["month"]] += D(row["income_delta"])
    variances = defaultdict(D)
    for row in replayed["prior_baseline_variances"]:
        variances[row["month"]] += D(row["income_delta"])
    for identity, months in [
        ("grove-base-cash-capital", monthly),
        ("grove-base-published-boundary", variances),
    ]:
        value = sum(months.values(), D(0))
        result["items"].append(
            dict(
                id=identity,
                prime="grove",
                field="sv_adj",
                unrounded=str(value),
                amount=str(value.quantize(D(".01"), rounding=ROUND_HALF_UP)),
                months={m: str(v) for m, v in months.items()},
                source="reconciliation/grove-base-cash-2026/inputs.json",
                prior_trueup="0",
                e43_missing_venue_credit_rebooked=False,
            )
        )
    total = {
        f: sum(
            (D(r["amount"]) for r in result["items"] if r["prime"] == "grove" and r["field"] == f),
            D(0),
        )
        for f in FIELDS
    }
    old = saved["adjustment_totals"]["grove"]
    for f, v in total.items():
        result["adjustment_totals"]["grove"][f] = str(v)
        result["remaining_to_add_to_october"]["grove"][f] = str(
            v - D(saved["already_in_october"]["grove"][f])
        )
        ordinary = D(saved["proposed_combined_october_adjustments"]["grove"][f]) - D(old[f])
        result["proposed_combined_october_adjustments"]["grove"][f] = str(ordinary + v)
    result["linear_settlement_effects"]["grove"] = dict(
        mint_delta=str(total["sky_adj"] + total["sv_adj"]),
        transfer_delta=str(total["dv_adj"] + total["sv_adj"] + total["send_credit"]),
        sky_net_delta=str(total["sky_adj"] - total["dv_adj"] - total["send_credit"]),
    )
    for prime in ["spark", "osero", "obex"]:
        for key in [
            "adjustment_totals",
            "remaining_to_add_to_october",
            "proposed_combined_october_adjustments",
            "linear_settlement_effects",
        ]:
            if result[key][prime] != saved[key][prime]:
                raise ValueError("Non-Grove amounts changed")
        if [r for r in result["items"] if r["prime"] == prime] != [
            r for r in saved["items"] if r["prime"] == prime
        ]:
            raise ValueError("Non-Grove items changed")
    result["calculation_commits"] = {
        p: (MAIN if p == "grove" else saved["calculation_commit"])
        for p in saved["adjustment_totals"]
    }
    result["calculation_commit"] = MAIN
    result["grove_pr280_replay"] = dict(
        scope=replayed["replay_filter"],
        active_venue_months=17,
        method_increment=replayed["income_delta"]["jan_sep"],
        published_baseline_increment=replayed["historical_increment_to_existing_reconciliation"],
        non_grove_results_unchanged=True,
        e12_global_cof_delta="0",
    )
    result["previous_scope_input_sha256"] = result.pop("input_sha256")
    paths = [
        checkpoint,
        ROOT / "reconciliation/grove-base-cash-2026/inputs.json",
        ROOT / "reconciliation/grove-base-cash-2026/baseline-netting.json",
        ROOT / "scripts/reconcile_grove_base_cash.py",
        Path(__file__),
    ]
    result["input_sha256"] = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }
    result["execution_status"] = (
        "Grove-only active-position replay complete; other-prime figures preserved; no production booking or execution."
    )
    return result
