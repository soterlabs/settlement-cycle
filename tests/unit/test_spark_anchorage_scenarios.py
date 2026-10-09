import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_anchorage_scenarios import (
    ACCOUNT,
    ALM,
    CASH,
    ESCROW,
    RETURNS,
    SCENARIOS,
    apply_anchorage_scenario,
)
from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory

EVIDENCE = json.loads(
    (Path(__file__).parents[1] / "fixtures/spark_anchorage_ambiguous_returns.json").read_text()
)


def batch(identity, timestamp, movements, minted=D(0), block=1):
    return B(
        identity=identity,
        day=datetime.fromtimestamp(timestamp, UTC).date(),
        timestamp=timestamp,
        chain="ethereum",
        block=block,
        log_index=0,
        minted=minted,
        movements=tuple(movements),
        minted_by_ilk={"A": minted},
    )


def history():
    start = RETURNS[0][2] - 100
    rows = [
        batch(
            "draw", start, [M(ACCOUNT, D(0), D("100000000"), preserve_basis=True)], D("100000000")
        )
    ]
    invested = D(0)
    for identity, block, timestamp, amount in RETURNS:
        rows.append(batch(identity, timestamp, [M(CASH, D(0), amount)], block=block))
        rows.append(
            batch(
                identity + "spend",
                timestamp + 1,
                [M(CASH, amount, -amount), M("investment", invested, amount)],
            )
        )
        invested += amount
    # A later deposit retains the old snapshot's undiscounted opening value.
    rows.append(
        batch(
            "deposit",
            RETURNS[-1][2] + 2,
            [M(ACCOUNT, D("100000000"), D("5000000"), preserve_basis=True)],
            D("5000000"),
        )
    )
    return CapitalHistory(tuple(rows), {"S23": ACCOUNT}, {"S23": "Unconfirmed split"})


def test_primary_cash_evidence_matches_scenario_guards():
    assert len(EVIDENCE["rows"]) == len(RETURNS)
    for row, (tx, block, timestamp, amount) in zip(EVIDENCE["rows"], RETURNS, strict=True):
        assert "ethereum:" + row["transaction_hash"] == tx
        assert (row["block_number"], row["block_time"]) == (block, timestamp)
        assert "0x" + row["topic1"][-40:] == ESCROW
        assert "0x" + row["topic2"][-40:] == ALM
        assert D(int(row["data"], 16)) / 10**6 == amount
        assert row["address"] == CASH.split(":")[-1]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_splits_conserve_cash_and_debt_and_rebase_future_marks(scenario):
    old = history()
    fixed = apply_anchorage_scenario(old, scenario)
    principal = sum(SCENARIOS[scenario])
    assert fixed.unsupported == old.unsupported
    assert [b.minted_by_ilk for b in old.batches] == [b.minted_by_ilk for b in fixed.batches]
    assert fixed.batches[-1].movements[0].value_before == D("100000000") - principal
    earned = sum(m.external_income for b in fixed.batches for m in b.movements)
    assert earned + principal == sum(r[3] for r in RETURNS)
    replay = replay_history(fixed, date(2026, 8, 1), date(2026, 8, 31))
    assert replay.ledger.drawn == D("105000000")
    assert replay.ledger.account(ACCOUNT).borrowed == D("105000000") - principal
    assert ACCOUNT in replay.uncertain_accounts
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
    assert sum(a.borrowed for a in replay.ledger.accounts.values()) == D("105000000")
    with pytest.raises(ValueError, match="already applied"):
        apply_anchorage_scenario(fixed, scenario)


def test_rejects_missing_history_wrong_cash_and_insufficient_funding():
    h = history()
    with pytest.raises(ValueError, match="Both Anchorage"):
        apply_anchorage_scenario(replace(h, batches=h.batches[:2]), "10m-50m")
    rows = list(h.batches)
    rows[1] = replace(rows[1], movements=(M(CASH, D(0), D(1)),))
    with pytest.raises(ValueError, match="cash shape"):
        apply_anchorage_scenario(replace(h, batches=tuple(rows)), "10m-50m")
    rows = list(h.batches)
    rows[0] = replace(rows[0], movements=(M(ACCOUNT, D(0), D(1), preserve_basis=True),))
    with pytest.raises(ValueError, match="exceeds observed"):
        apply_anchorage_scenario(replace(h, batches=tuple(rows)), "10m-50m")
