import gzip
import importlib.util
import json
import sys
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "savings_principal", ROOT / "scripts/audit_spark_savings_principal.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
sys.path.pop(0)


@pytest.fixture(scope="module")
def evidence():
    def read(name):
        p = ROOT / "tests/fixtures" / name
        return json.loads(gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes())

    return (
        read("spark_savings_v2_liability.json.gz"),
        read("spark_savings_v2_rates.json"),
        read("spark_savings_v2_funding.json.gz"),
    )


def test_rate_history_reproduces_every_drip_and_final_state_and_preserves_cash(evidence):
    groups, rates, cash = evidence
    results = [
        audit.split_vault(g, next(r for r in rates if r["venue"] == g["venue"]), cash)
        for g in groups
    ]
    assert sum(r["rate_events"] for r in results) == 841
    for r in results:
        assert abs(D(r["conservation_residual"])) < D("1e-40")
        assert D(r["principal"]) >= 0 and D(r["interest"]) >= 0 and D(r["prepaid"]) >= 0
        assert D(r["borrowed_principal"]) - D(r["returned_principal"]) == pytest.approx(
            D(r["principal"]), abs=D("1e-15")
        )
    # A $400m Sky draw repays saver funding; only its principal part can
    # replace saver funding of investments. The remainder pays VSR interest.
    tx = "0x3267f7a7508ad892778e1afafb965ecb12660d5d0c8e9fcc278210a19d07ccf6"
    ops = {
        o["kind"]: D(o["amount"]) for o in results[0]["operations"] if o["transaction_hash"] == tx
    }
    assert ops["repay"].quantize(D(".000001")) == D("398895575.396220")
    assert ops["interest"].quantize(D(".000001")) == D("1094157.451725")
    assert ops["repay"] + ops["interest"] == D("399989732.847945")
    # PYUSD is economically fully returned; retain any sub-unit policy fraction.
    assert D(results[2]["principal"]) < D("1e-20")
    assert D(results[2]["interest"]) > 0
    # Exact payment-time accrual removes the apparent overpayments produced
    # by using stale Drip balances. No prepayment assumption is needed here.
    assert not any(
        o["kind"] in ("prepay", "refund", "prepaid_interest")
        for r in results
        for o in r["operations"]
    )


def test_missing_rate_change_is_detected_by_contract_accrual(evidence):
    groups, rates, cash = evidence
    bad = deepcopy(rates[0])
    bad["rows"].pop(0)
    with pytest.raises(ValueError, match=r"rate update|rate schedule"):
        audit.split_vault(groups[0], bad, cash)


def test_wrong_pin_is_not_accepted(evidence):
    groups, rates, cash = evidence
    bad = deepcopy(rates[0])
    bad["pin"] += 1
    with pytest.raises(ValueError, match="different vault/pin"):
        audit.split_vault(groups[0], bad, cash)


def test_ray_rounding_and_zero_power():
    assert audit.rpow(0, 0) == audit.RAY
    assert audit.rpow(0, 10) == 0
    assert audit.rpow(audit.RAY, 12345) == audit.RAY
    assert audit.rpow(2 * audit.RAY, 3) == 8 * audit.RAY
    x = audit.RAY + 10**14
    assert audit.rpow(x, 2) == (x * x + audit.RAY // 2) // audit.RAY
