import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_paxos_capital import (
    ACCOUNT,
    ENTRY,
    EVENTS,
    HOLDER,
    MARKER,
    PAYER,
    PYUSD,
    USDC,
    VENUE,
    link_spark_paxos,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory


@pytest.fixture(scope="module")
def events():
    evidence = json.loads(
        (
            Path(__file__).parents[2] / "tests/fixtures/spark_paxos_pyusd_usdc_events.json"
        ).read_text()
    )
    decoded = []
    for r in evidence["rows"]:
        sender, recipient = "0x" + r["topic1"][-40:], "0x" + r["topic2"][-40:]
        deposit = r["address"] == PYUSD
        assert (sender, recipient) == ((HOLDER, ENTRY) if deposit else (PAYER, HOLDER))
        assert r["address"] in (PYUSD, USDC)
        decoded.append(
            (
                r["transaction_hash"],
                r["block_number"],
                r["block_time"],
                r["log_index"],
                int(r["data"], 16),
                deposit,
            )
        )
    assert tuple(sorted(decoded, key=lambda e: (e[1], e[3]))) == EVENTS
    return EVENTS


def synthetic_history(events):
    prefix = "ethereum:" + HOLDER + ":"
    paid = sum((D(e[4]) / 10**6 for e in events if e[5]), D(0))
    first = events[0]
    day = datetime.fromtimestamp(first[2], UTC).date()
    # Isolated funding test, not a claim that this Sky draw happened on-chain.
    batches = [
        CapitalBatch(
            "synthetic-seed",
            day,
            first[2] - 1,
            "ethereum",
            first[1] - 1,
            (AssetMovement(prefix + PYUSD, D(0), paid),),
            paid,
            minted_by_ilk={"test": paid},
        )
    ]
    pyusd, cash = paid, D(0)
    for tx, block, stamp, index, raw, deposit in events:
        amount = D(raw) / 10**6
        movement = AssetMovement(
            prefix + (PYUSD if deposit else USDC),
            pyusd if deposit else cash,
            -amount if deposit else amount,
        )
        if deposit:
            pyusd -= amount
        else:
            cash += amount
        batches.append(
            CapitalBatch(
                "ethereum:" + tx,
                datetime.fromtimestamp(stamp, UTC).date(),
                stamp,
                "ethereum",
                block,
                (movement,),
                log_index=index,
            )
        )
    return CapitalHistory(tuple(batches), {"S15": prefix + USDC, "S28": prefix + PYUSD}, {})


def test_complete_boundary_history_conserves_funding_and_leaves_shortfall_open(events):
    assert sum(e[5] for e in events) == 287
    assert sum(not e[5] for e in events) == 287
    history = synthetic_history(events)
    patched = link_spark_paxos(history)
    assert link_spark_paxos(patched) == patched
    assert patched.venue_accounts[VENUE] == ACCOUNT
    assert VENUE in patched.analytics_only_venues
    result = replay_history(patched, history.batches[0].day, history.batches[-1].day)
    assert not result.unmatched_receipts and not result.unmatched_outflows
    boundary = result.ledger.account(ACCOUNT)
    assert boundary.value == D("12.378699")
    assert abs(boundary.borrowed - boundary.value) < D("1e-40")
    assert result.ledger.drawn == D("417761317.838699")
    assert sum(a.borrowed for a in result.ledger.accounts.values()) == pytest.approx(
        result.ledger.drawn, abs=D("1e-15")
    )
    assert ACCOUNT in result.uncertain_accounts  # Explicit reviewed association.
    for old, new in zip(history.batches, patched.batches, strict=True):
        assert old.minted == new.minted and old.minted_by_ilk == new.minted_by_ilk
        assert new.movements[: len(old.movements)] == old.movements


def test_incomplete_history_and_changed_cash_are_rejected(events):
    history = synthetic_history(events)
    missing = replace(history, batches=(history.batches[0], *history.batches[2:]))
    with pytest.raises(ValueError, match="Missing Spark Paxos"):
        link_spark_paxos(missing)
    rows = list(history.batches)
    index = next(i for i, b in enumerate(rows) if b.identity == "ethereum:" + events[1][0])
    row = rows[index]
    rows[index] = replace(row, movements=(replace(row.movements[0], change=D(1)),))
    with pytest.raises(ValueError, match="cash shape changed"):
        link_spark_paxos(replace(history, batches=tuple(rows)))


def test_other_prime_and_partial_transformation_do_not_get_spark_funding(events):
    history = synthetic_history(events)
    other = replace(history, venue_accounts={"E1": "ethereum:other:" + USDC})
    assert link_spark_paxos(other) == other
    done = link_spark_paxos(history)
    mixed = replace(done, batches=(*done.batches, history.batches[1]))
    with pytest.raises(ValueError, match="Partially applied"):
        link_spark_paxos(mixed)
    assert all(b.identity.endswith(MARKER) for b in done.batches[1:])
