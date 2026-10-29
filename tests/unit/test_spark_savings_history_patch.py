import importlib.util
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_capital import CapitalBatch, CapitalHistory

spec = importlib.util.spec_from_file_location(
    "savings_patch", Path(__file__).parents[2] / "scripts/patch_spark_savings_funding.py"
)
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def test_savings_operations_follow_reviewed_boundary_suffix_without_losing_policy(monkeypatch):
    batch = CapitalBatch(
        "ethereum:0xabc:paxos-pyusd-usdc",
        date(2026, 5, 28),
        100,
        "ethereum",
        10,
        (),
        funding_assumption="Reviewed Paxos association",
    )
    history = CapitalHistory((batch,), {}, {})
    monkeypatch.setattr(patch, "apply_executed_spells", lambda h: h)
    audit = {
        "policy": "PROVISIONAL: proportional",
        "vaults": [
            {
                "operations": [
                    {
                        "kind": "draw",
                        "source": "ethereum:vault",
                        "amount": "20",
                        "transaction_hash": "0xabc",
                        "block": 10,
                        "timestamp": 100,
                        "log_index": 1,
                    }
                ]
            }
        ],
    }
    result = patch.attach(history, audit)
    assert result.batches[0].external_funding[0].amount == D(20)
    assert result.batches[0].minted == batch.minted
    assert "Reviewed Paxos association" in result.batches[0].funding_assumption
    assert patch.POLICY in result.batches[0].funding_assumption
    with pytest.raises(ValueError, match="already attached"):
        patch.attach(result, audit)


def test_ambiguous_split_transaction_is_not_pooled_automatically(monkeypatch):
    day = date(2026, 5, 28)
    history = CapitalHistory(
        tuple(
            CapitalBatch("ethereum:0xabc:" + suffix, day, 100, "ethereum", 10, ())
            for suffix in ("one", "two")
        ),
        {},
        {},
    )
    monkeypatch.setattr(patch, "apply_executed_spells", lambda h: h)
    audit = {
        "policy": "PROVISIONAL: proportional",
        "vaults": [
            {
                "operations": [
                    {
                        "kind": "draw",
                        "source": "ethereum:vault",
                        "amount": "20",
                        "transaction_hash": "0xabc",
                        "block": 10,
                        "timestamp": 100,
                        "log_index": 1,
                    }
                ]
            }
        ],
    }
    with pytest.raises(ValueError, match="unambiguous normalized route"):
        patch.attach(history, audit)
