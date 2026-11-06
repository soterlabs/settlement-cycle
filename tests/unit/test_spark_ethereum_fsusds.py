import gzip
import importlib.util
import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location(
    "ethereum_fsusds_repair", ROOT / "scripts/repair_spark_ethereum_fsusds.py"
)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
EVIDENCE = json.loads(
    gzip.decompress((ROOT / "tests/fixtures/spark_ethereum_fsusds.json.gz").read_bytes())
)


def test_all_seven_exchanges_match_actual_susds_cash_and_leave_debt_unchanged():
    assert len(EVIDENCE) == 7
    for witness in EVIDENCE:
        before = witness["batch"]
        after, change = repair.repair(before, witness)
        assert abs(D(change["new_signed_residual"])) < D("1e-8")
        assert abs(D(change["old_signed_residual"])) > 10000
        assert {k: v for k, v in after.items() if k != "movements"} == {
            k: v for k, v in before.items() if k != "movements"
        }
        for old, new in zip(before["movements"], after["movements"], strict=True):
            if old["account"] != repair.ACCOUNT:
                assert old == new
            else:
                assert D(new['external_income']) == D(old['external_income']) == 0
        with pytest.raises(ValueError, match="underlying-at-par"):
            repair.repair(after, witness)


def test_incorrect_asset_and_cash_witness_are_rejected():
    witness = deepcopy(EVIDENCE[0])
    witness["asset"] = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
    with pytest.raises(ValueError, match="actual asset changed"):
        repair.repair(witness["batch"], witness)
    witness = deepcopy(EVIDENCE[0])
    witness["receipt"]["logs"] = [
        r for r in witness["receipt"]["logs"] if r["address"] != repair.SUSDS
    ]
    with pytest.raises(ValueError, match="underlying cash disagrees"):
        repair.repair(witness["batch"], witness)
