import gzip
import importlib.util
import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location(
    "ethena_audit", ROOT / "scripts/audit_spark_ethena_execution.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
ROWS = json.loads(
    gzip.decompress((ROOT / "tests/fixtures/spark_ethena_execution_events.json.gz").read_bytes())
)


def test_all_executions_match_actual_cash_and_explain_the_shortfalls():
    records = audit.audit(ROWS)
    assert len(records) == 856
    assert sum(r["kind"] == "Mint" for r in records) == 216
    assert sum(r["kind"] == "Redeem" for r in records) == 640
    assert sum(D(r["par_value_shortfall"]) for r in records) == D("2224865.73335")
    chosen = next(
        r
        for r in records
        if r["identity"].endswith(
            "94ce2896098f7e11f245fb9d1185cdaf0d30bc68928524fab5b1a4878c4fb983"
        )
    )
    assert D(chosen["collateral_amount"]) == 20000000
    assert D(chosen["usde_amount"]) == D("19981978.60")
    assert audit.audit(ROWS + ROWS) == records


def test_missing_cash_or_conflicting_log_cannot_prove_a_shortfall():
    first = next(r for r in ROWS if r["topic0"] == audit.TRANSFER_TOPIC0)
    without = [r for r in ROWS if r is not first]
    with pytest.raises(ValueError, match="actual ALM cash"):
        audit.audit(without)
    copy = deepcopy(first)
    copy["data"] = "0x" + "0" * 64
    with pytest.raises(ValueError, match="Conflicting"):
        audit.audit([*ROWS, copy])
