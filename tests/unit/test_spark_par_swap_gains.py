import gzip
import importlib.util
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_par_swap_gains import MARKER, recognize_spark_par_swap_gains, rules
from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory
from settle.normalize.spark_par_swap_evidence import CURVE, HOLDER, PM, TRANSFER_TOPIC0, prove_swaps

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location(
    "par_swap_audit", ROOT / "scripts/audit_spark_par_swaps.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
EVIDENCE = json.loads(
    gzip.decompress((ROOT / "tests/fixtures/spark_par_swap_events.json.gz").read_bytes())
)


def test_registry_is_reproduced_from_events_and_complete_actual_cash_deltas():
    verified, excluded, derived = audit.audited_rules(EVIDENCE)
    assert derived == list(rules())
    assert len(verified) == 8550 and len(excluded) == 515
    assert len(derived) == 1871
    assert sum(D(r["earned"]) for r in derived) == D("145059.266374643850597556")
    assert {r["reason"] for r in excluded} == {"liquidity modification", "cash mismatch"}


def test_missing_cash_is_excluded_and_conflicting_evidence_rejected():
    rule = rules()[0]
    rows = [
        r
        for r in EVIDENCE["raw"]["rows"]
        if "ethereum:" + r["transaction_hash"] == rule["identity"]
    ]
    raw = {**EVIDENCE["raw"], "rows": rows}
    assert len(prove_swaps(raw, EVIDENCE["metadata"])[0]) == 1
    payment = next(
        r
        for r in rows
        if r["topic0"] == TRANSFER_TOPIC0
        and "0x" + r["topic1"][-40:] in (PM, CURVE)
        and "0x" + r["topic2"][-40:] == HOLDER
    )
    missing = {**raw, "rows": [r for r in rows if r is not payment]}
    verified, excluded = prove_swaps(missing, EVIDENCE["metadata"])
    assert not verified and excluded[0]["reason"] == "cash mismatch"
    bad = deepcopy(payment)
    bad["data"] = "0x" + "0" * 64
    with pytest.raises(ValueError, match="Conflicting swap"):
        prove_swaps({**raw, "rows": [*rows, bad]}, EVIDENCE["metadata"])
    metadata = deepcopy(EVIDENCE["metadata"])
    next(iter(metadata["v4_pools"].values()))["fee"] += 1
    with pytest.raises(ValueError, match="pool key mismatch"):
        prove_swaps(raw, metadata)


def history():
    r = next(r for r in rules() if D(r["change"]) > D(r["earned"]))
    day = datetime.fromtimestamp(r["timestamp"], UTC).date()
    received = D(r["change"])
    paid = received - D(r["earned"])
    funding = B(
        "funding",
        day,
        r["timestamp"] - 1,
        "ethereum",
        r["block"] - 1,
        (M("source", D(0), paid),),
        paid,
        minted_by_ilk={"A": paid},
    )
    trade = B(
        r["identity"],
        day,
        r["timestamp"],
        "ethereum",
        r["block"],
        (M("source", paid, -paid), M(r["account"], D(0), received)),
    )
    return CapitalHistory((funding, trade), {"Sx": r["account"]}, {}), r


def test_normal_dispatch_classifies_only_earnings_and_never_increases_sky_principal():
    h, r = history()
    fixed = recognize_spark_par_swap_gains(h)
    assert recognize_spark_par_swap_gains(fixed) == fixed
    assert fixed.batches[1].identity == r["identity"] + MARKER
    assert fixed.batches[1].minted_by_ilk == h.batches[1].minted_by_ilk
    assert fixed.batches[1].movements[0] == h.batches[1].movements[0]
    replay = replay_history(h, h.batches[0].day, h.batches[-1].day)
    paid = D(r["change"]) - D(r["earned"])
    assert replay.ledger.drawn == paid
    assert abs(replay.ledger.account(r["account"]).borrowed - paid) < D("1e-18")
    assert replay.ledger.account(r["account"]).value == D(r["change"])
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
    assert not replay.uncertain_accounts
    assert recognize_spark_par_swap_gains(
        replace(h, venue_accounts={"E1": "grove:cash"})
    ) == replace(h, venue_accounts={"E1": "grove:cash"})
    broken = replace(h.batches[-1], timestamp=0)
    with pytest.raises(ValueError, match="metadata changed"):
        recognize_spark_par_swap_gains(replace(h, batches=(*h.batches[:-1], broken)))
    with pytest.raises(ValueError, match="Mixed Spark"):
        recognize_spark_par_swap_gains(replace(fixed, batches=(*fixed.batches, h.batches[-1])))


def test_swap_gain_can_pay_debt_without_leaving_an_ending_cash_balance():
    rule = next(r for r in rules() if D(r["change"]) == 0)
    gain = D(rule["earned"])
    day = datetime.fromtimestamp(rule["timestamp"], UTC).date()
    principal = D(1000)
    borrowed = principal + gain
    funded = B(
        "funding",
        day,
        rule["timestamp"] - 1,
        "ethereum",
        rule["block"] - 1,
        (M("investment", D(0), principal), M("other-cash", D(0), gain)),
        borrowed,
        minted_by_ilk={"A": borrowed},
    )
    trade = B(
        rule["identity"],
        day,
        rule["timestamp"],
        "ethereum",
        rule["block"],
        (M("investment", principal, -principal), M(rule["account"], D(0), D(0))),
        -borrowed,
        minted_by_ilk={"A": -borrowed},
    )
    h = CapitalHistory((funded, trade), {"Sx": rule["account"]}, {})
    result = replay_history(h, day, day)
    assert not result.unmatched_receipts and not result.unmatched_outflows
    assert result.ledger.drawn == result.ledger.repaid == borrowed
    assert result.ledger.account(rule["account"]).value == 0
    assert result.ledger.account("other-cash").value == gain
    assert abs(sum(a.borrowed for a in result.ledger.accounts.values())) < D("1e-18")
