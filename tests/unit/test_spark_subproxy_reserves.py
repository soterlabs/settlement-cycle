import importlib.util
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_subproxy_reserve_gifts import (
    LEGS,
    PREFIX,
    TX,
    recognize_spark_subproxy_reserves,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location(
    "subproxy_audit", ROOT / "scripts/audit_spark_subproxy_reserves.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
EVIDENCE = json.loads((ROOT / "tests/fixtures/spark_subproxy_reserve_history.json").read_text())


def history():
    row = dict(EVIDENCE["execution"]["batch"])
    row["day"] = date.fromisoformat(row["day"])
    row["minted"] = D(row["minted"])
    row["minted_by_ilk"] = {k: D(v) for k, v in row["minted_by_ilk"].items()}
    row["external_funding"] = ()
    row["movements"] = tuple(
        AssetMovement(
            m["account"],
            D(m["value_before"]),
            D(m["change"]),
            D(m["external_income"]),
            m["preserve_basis"],
        )
        for m in row["movements"]
    )
    return CapitalHistory((CapitalBatch(**row),), {token: PREFIX + token for token, *_ in LEGS}, {})


def test_complete_history_proves_earned_fraction_but_not_the_two_seed_mints():
    rows = audit.audit(EVIDENCE)
    assert len(EVIDENCE["rows"]) == 12
    for row, leg in zip(rows, LEGS, strict=True):
        assert row["token"] == leg[0] and D(row["earned_value"]) == leg[3]
    assert sum(D(r["unclassified_seed_value"]) for r in rows).quantize(D(".000001")) == D(
        "2.149291"
    )
    assert all(v == 0 for v in EVIDENCE["closing_scaled"].values())
    broken = deepcopy(EVIDENCE)
    broken["rows"] = broken["rows"][1:]
    with pytest.raises(ValueError, match="does not exhaust"):
        audit.audit(broken)


def test_only_forwarded_earnings_are_added_once_and_no_sky_basis_is_created():
    before = history()
    after = recognize_spark_subproxy_reserves(before)
    assert recognize_spark_subproxy_reserves(after) == after
    added = D(0)
    for old, new in zip(before.batches[0].movements, after.batches[0].movements, strict=True):
        assert replace(old, external_income=new.external_income) == new
        added += new.external_income - old.external_income
    assert added.quantize(D(".000001")) == D("1217604.223779")
    b = after.batches[0]
    replay = replay_history(after, b.day, b.day)
    assert sum(replay.unmatched_receipts.values()).quantize(D(".000001")) == D("2.149291")
    assert replay.ledger.drawn == 0
    assert all(a.borrowed == 0 for a in replay.ledger.accounts.values())


def test_shape_and_prime_guards():
    h = history()
    b = h.batches[0]
    with pytest.raises(ValueError, match="metadata changed"):
        recognize_spark_subproxy_reserves(replace(h, batches=(replace(b, block=b.block + 1),)))
    other = replace(h, venue_accounts={"E1": "ethereum:grove:token"})
    assert recognize_spark_subproxy_reserves(other) == other
    assert b.identity == TX
