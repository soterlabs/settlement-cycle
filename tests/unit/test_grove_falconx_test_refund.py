import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_falconx_test_refund import (
    ACCOUNT,
    AUSD,
    CASH,
    REFUNDS,
    SOURCE,
    link_falconx_test_refund,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/grove_falconx_test_refund_events.json').read_text())


def test_test_payment_forward_and_two_returns_match_canonical_logs():
    assert len(ROWS) == 4
    deposit = next(r for r in ROWS if r['transaction_hash'] == SOURCE.split(':')[1])
    relay = next(r for r in ROWS if r['topic1'].endswith('d94f9ef3395bbe41c1f05ced3c9a7dc520d08036'))
    assert deposit['topic2'] == relay['topic1']
    assert int(deposit['data'], 16) == int(relay['data'], 16) == 1000 * 10**6
    assert relay['topic2'].endswith('1157a2076b9bb22a85cc2c162f20fab3898f4101')
    for identity, block, amount in REFUNDS:
        r = next(r for r in ROWS if r['transaction_hash'] == identity.split(':')[1])
        assert r['topic1'] == relay['topic2'] and r['topic2'] == deposit['topic1']
        assert r['block_number'] == block and D(int(r['data'],16))/10**6 == amount
        assert deposit['block_number'] < relay['block_number'] < block
    assert sum(r[2] for r in REFUNDS) == D('999.999999')


def history():
    deposit = next(r for r in ROWS if r['transaction_hash'] == SOURCE.split(':')[1])
    day = datetime.fromtimestamp(deposit['block_time'], UTC).date()
    batches = [CapitalBatch(SOURCE, day, deposit['block_time'], 'ethereum', deposit['block_number'],
                           (AssetMovement(ACCOUNT, D(0), D(1000)),), D(1000))]
    balance = D(0)
    for identity, block, amount in REFUNDS:
        r = next(r for r in ROWS if r['transaction_hash'] == identity.split(':')[1])
        batches.append(CapitalBatch(identity, day, r['block_time'], 'ethereum', block,
                                    (AssetMovement(CASH, balance, amount),)))
        balance += amount
    t, block = batches[-1].timestamp, batches[-1].block
    # Reallocate the refunded cash into the next deposit; the old boundary
    # incorrectly still included the original test principal as outstanding.
    batches.extend([
        CapitalBatch('production', day, t+1, 'ethereum', block+1,
            (AssetMovement(CASH, balance, -balance), AssetMovement(ACCOUNT, D(1000), balance))),
        CapitalBatch('return', day, t+2, 'ethereum', block+2,
            (AssetMovement(AUSD, D(0), D(2100), external_income=D('100.000001')),
             AssetMovement(ACCOUNT, D('1999.999999'), D('-1999.999999')))),
    ])
    return CapitalHistory(tuple(batches), {'E36': ACCOUNT, 'E15': CASH, 'E14': AUSD}, {})


def test_refund_recycles_existing_basis_and_adjusts_later_principal_cap_once():
    h = history()
    fixed = link_falconx_test_refund(h)
    assert fixed == link_falconx_test_refund(fixed)
    assert fixed.batches[-1].movements[0].external_income == D(1100)
    r = replay_history(fixed, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.drawn == D(1000)
    assert r.ledger.account(ACCOUNT).borrowed == 0
    assert r.ledger.account(AUSD).borrowed == D(1000)
    assert r.ledger.account(AUSD).value == D(2100)
    assert not r.unmatched_receipts and not r.unmatched_outflows


def test_cutoff_after_first_refund_retains_outstanding_test_principal():
    h = history()
    h = replace(h, batches=h.batches[:2])
    fixed = link_falconx_test_refund(h)
    r = replay_history(fixed, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.account(ACCOUNT).borrowed == D('989.9974')
    assert r.ledger.account(CASH).borrowed == D('10.0026')
    assert len(fixed.batches) == 2


def test_changed_refund_or_missing_test_deposit_is_rejected():
    h = history()
    b = h.batches[1]
    bs = list(h.batches)
    bs[1] = replace(b, movements=(replace(b.movements[0], change=D(11)),))
    with pytest.raises(ValueError, match='differs'):
        link_falconx_test_refund(replace(h, batches=tuple(bs)))
    with pytest.raises(ValueError, match='lacks its reviewed'):
        link_falconx_test_refund(replace(h, batches=h.batches[1:]))
