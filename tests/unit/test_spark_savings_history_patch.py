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


def test_binance_savings_label_follows_only_the_reviewed_otc_branch(monkeypatch):
    from settle.compute.spark_binance_capital import MARKER, RULES, SAVER, SKY_MARKER
    tx, block, timestamp, _, _ = RULES[0]
    history = CapitalHistory(tuple(
        CapitalBatch(tx + suffix, date(2026, 8, 12), timestamp, "ethereum", block, ())
        for suffix in (MARKER, SKY_MARKER)
    ), {}, {})
    monkeypatch.setattr(patch, "apply_executed_spells", lambda h: h)
    op = dict(kind="draw", source=SAVER, amount="2000", transaction_hash=tx.split(':')[1],
              block=block, timestamp=timestamp, log_index=550)
    audit = dict(policy="PROVISIONAL: proportional", vaults=[dict(operations=[op])])
    result = patch.attach(history, audit)
    assert result.batches[0].external_funding[0].amount == D(2000)
    assert result.batches[1].external_funding == ()
    op['amount'] = '2001'
    with pytest.raises(ValueError, match="reviewed Savings funding changed"):
        patch.attach(history, audit)
