import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_plume_capital import (
    PENDING,
    PLUME,
    REDEMPTIONS,
    SOURCE,
    TRANSFERS,
    link_grove_plume_jtrsy,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/grove_plume_jtrsy_events.json').read_text())
TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
SEND = '0x305b9cb4e01c11504063f80b53afc4c8ddd761252d25d88c5be16daf7ee2ebab'
HANDLE = '0xfdc7fb3a9e7dfe684d8552aa3baec86425ed262d87167cd6ff908a6c0b58d09f'
REQUEST = '0x1fdc681a13d8c5da54e301c7ce6542dcde4581e4725043fdab2db12ddc574506'
WITHDRAW = '0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db'


def test_canonical_messages_match_and_redemptions_exhaust_actual_shares():
    for bo, txo, bi, txi, units in TRANSFERS:
        send = next(r for r in ROWS if r['transaction_hash'] == txo and r['topic0'] == SEND)
        receive = next(r for r in ROWS if r['transaction_hash'] == txi and r['topic0'] == HANDLE)
        assert send['chain'] == 'ethereum' and send['block_number'] == bo
        assert receive['chain'] == 'plume' and receive['block_number'] == bi
        assert int(send['topic1'], 16) == 4 and int(receive['topic1'], 16) == 1
        assert send['data'] == receive['data']  # Exact encoded message, not USD matching.
        for tx, token in ((txo, SOURCE.split(':')[2]), (txi, '0xa5d465251fbcc907f5dd6bb2145488dfc6a2627b')):
            transfers = [r for r in ROWS if r['transaction_hash'] == tx and r['address'] == token
                         and r['topic0'] == TRANSFER]
            assert transfers and all(int(r['data'], 16) == units for r in transfers)
        mint = next(r for r in ROWS if r['transaction_hash'] == txi and r['topic0'] == TRANSFER)
        assert int(mint['topic1'], 16) == 0 and mint['topic2'][-40:] == PLUME[2:]
    requests = [r for r in ROWS if r['topic0'] == REQUEST]
    for block, tx, units, cash in REDEMPTIONS:
        withdrawals = [r for r in ROWS if r['transaction_hash'] == tx and r['topic0'] == WITHDRAW]
        assert len(withdrawals) == 1
        row = withdrawals[0]
        words = [int(row['data'][i:i + 64], 16) for i in range(2, len(row['data']), 64)]
        assert row['block_number'] == block
        assert D(words[0]) / 10**6 == D(cash)
        assert words[1] == units + 1  # Withdraw's round-up, not actual shares burned.
        request = next(r for r in requests if int(r['data'][-64:], 16) == units)
        assert request['block_time'] < row['block_time']
        burns = [r for r in ROWS if r['topic0'] == TRANSFER and r['address'] == '0xa5d465251fbcc907f5dd6bb2145488dfc6a2627b'
                 and int(r['topic2'], 16) == 0 and int(r['data'], 16) == units]
        assert len(burns) == 1 and request['block_time'] < burns[0]['block_time'] < row['block_time']
    assert sum(t[4] for t in TRANSFERS) == sum(r[2] for r in REDEMPTIONS)


def history():
    day = date(2025, 10, 1)
    batches = [CapitalBatch('fund', day, 1, 'ethereum', 1,
                           (AssetMovement(SOURCE, D(0), D(100_000_000)),), D(100_000_000))]
    value = D(100_000_000)
    def batch(tx, chain, block, movements=(), minted=D(0)):
        timestamp = next(r['block_time'] for r in ROWS if r['transaction_hash'] == tx)
        return CapitalBatch(chain + ':' + tx, date(2025, 10, 16), timestamp, chain, block,
                            movements, minted)
    for n, (bo, txo, bi, txi, units) in enumerate(TRANSFERS):
        amount = D(units) / 10**6
        movement = (AssetMovement(SOURCE, value, -amount),)
        if n == 2:
            movement += (AssetMovement('unrelated', D(0), D(7)),)
        batches += [batch(txo, 'ethereum', bo, movement, D(7) if n == 2 else D(0)),
                    batch(txi, 'plume', bi)]
        value -= amount
    for block, tx, _, cash in REDEMPTIONS:
        batches.append(batch(tx, 'plume', block, (AssetMovement('cash:' + tx, D(0), D(cash)),)))
    return CapitalHistory(tuple(batches), {'E9': SOURCE}, {})


def test_custody_route_preserves_basis_and_does_not_mix_unrelated_draw():
    h = history()
    linked = link_grove_plume_jtrsy(h)
    assert link_grove_plume_jtrsy(linked) == linked
    r = replay_history(linked, date(2025, 10, 16), date(2025, 10, 16))
    received = sum(r.ledger.account('cash:' + tx).borrowed for _, tx, _, _ in REDEMPTIONS)
    sent = sum(D(t[4]) / 10**6 for t in TRANSFERS)
    assert abs(received - sent) < D('1e-18')
    assert r.ledger.account(PENDING).borrowed == 0
    assert r.ledger.account('unrelated').borrowed == 7
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert r.ledger.realised_principal_loss == 0


def test_pinned_pending_route_and_missing_source_or_delivery_rejected():
    h = history()
    source = 'ethereum:' + TRANSFERS[0][1]
    pending = replace(h, batches=tuple(b for b in h.batches if b.identity in ('fund', source)))
    r = replay_history(pending, date(2025, 10, 16), date(2025, 10, 16))
    assert r.ledger.account(PENDING).borrowed == D(TRANSFERS[0][4]) / 10**6
    for identity in (source, 'plume:' + TRANSFERS[0][3]):
        with pytest.raises(ValueError, match='Grove Plume JTRSY'):
            link_grove_plume_jtrsy(replace(h, batches=tuple(b for b in h.batches if b.identity != identity)))
