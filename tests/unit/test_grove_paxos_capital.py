import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_paxos_capital import EVENTS, link_grove_paxos_boundary
from settle.extract.hypersync import LogRow
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory
from settle.normalize.allocation_paxos import (
    ACCOUNT,
    CASH,
    VENUE,
    link_paxos_boundary,
    paxos_events,
)

ROWS = [LogRow(**r) for r in json.loads(
    (Path(__file__).parents[1] / 'fixtures/grove_paxos_boundary_events.json').read_text())]
ILK = '0x' + b'ALLOCATOR-BLOOM-A'.hex().ljust(64, '0')


def history():
    batches = tuple(CapitalBatch('ethereum:' + tx,
        datetime.fromtimestamp(t, UTC).date(), t, 'ethereum', block,
        (AssetMovement(CASH, D(0), D(0)),), amount, log_index, {ILK: amount})
        for tx, block, t, log_index, amount in EVENTS)
    return CapitalHistory(batches, {'E15': CASH}, {})


def test_five_actual_alm_deposits_match_reviewed_history():
    assert paxos_events(ROWS) == list(EVENTS)
    assert sum(e[4] for e in EVENTS) == D('15000100')
    h = history()
    linked = link_grove_paxos_boundary(h)
    assert linked == link_grove_paxos_boundary(linked)
    r = replay_history(linked, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.account(ACCOUNT).borrowed_by_ilk == {ILK: D('15000100')}
    assert r.ledger.drawn == D('15000100')
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert linked.analytics_only_venues == (VENUE,)


def test_pinned_cutoff_cannot_pull_in_later_deposits():
    h = history()
    h = replace(h, batches=h.batches[:1])
    linked = link_grove_paxos_boundary(h)
    assert len(linked.batches) == 1
    assert linked.batches[0].movements[-1].change == D(100)


def test_return_releases_principal_and_only_excess_is_income():
    h = history()
    last = h.batches[-1]
    tx, amount = 'return', D('15010100')
    b = CapitalBatch('ethereum:' + tx, last.day, last.timestamp + 1,
        'ethereum', last.block + 1, (AssetMovement(CASH, D(0), amount),))
    h = replace(h, batches=(*h.batches, b))
    events = [*EVENTS, (tx, b.block, b.timestamp, 1, -amount)]
    fixed = link_paxos_boundary(h, events)
    r = replay_history(fixed, h.batches[0].day, last.day)
    assert r.ledger.account(ACCOUNT).borrowed == 0
    assert r.ledger.account(CASH).borrowed == D('15000100')
    assert r.ledger.account(CASH).value == amount
    assert fixed.batches[-1].movements[0].external_income == D(10000)
    assert not r.unmatched_receipts and not r.unmatched_outflows


def test_multiple_transfers_in_one_tx_make_one_position_movement():
    h = history()
    first = EVENTS[0]
    events = [(*first[:-1], D(40)), (*first[:3], first[3] + 1, D(60))]
    fixed = link_paxos_boundary(replace(h, batches=h.batches[:1]), events)
    assert len([m for m in fixed.batches[0].movements if m.account == ACCOUNT]) == 1
    assert fixed.batches[0].movements[-1].change == D(100)


def test_other_sender_token_or_recipient_is_not_a_boundary_transfer():
    r = ROWS[0]
    assert not paxos_events([replace(r, address='0x'+'11'*20)])
    assert not paxos_events([replace(r, topic1='0x'+'11'*32)])
    assert not paxos_events([replace(r, topic2='0x'+'11'*32)])


def test_cash_leg_or_transaction_mismatch_fails():
    h = history()
    b = h.batches[0]
    for changed in (replace(b, movements=()), replace(b, block=1)):
        with pytest.raises(ValueError):
            link_grove_paxos_boundary(replace(h, batches=(changed,)))
