import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_historical_capital import (
    AVALANCHE_SHARES,
    JAAA_TRANSFERS,
    SHARES,
    link_grove_jaaa_avalanche,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

DAY = date(2026, 8, 1)


def test_historical_bridge_carries_basis_through_quote_change_and_pending_exit():
    bo, txo, bi, txi = JAAA_TRANSFERS[0]
    history = CapitalHistory((
        CapitalBatch('funding', DAY, 1, 'ethereum', 1,
                     (AssetMovement(SHARES, D(0), D(100)),), D(100), minted_by_ilk={'A': D(100)}),
        CapitalBatch('ethereum:' + txo, DAY, 2, 'ethereum', bo,
                     (AssetMovement(SHARES, D(110), D(-55)),)),
        CapitalBatch('avalanche_c:' + txi, DAY, 3, 'avalanche_c', bi,
                     (AssetMovement(AVALANCHE_SHARES, D(0), D(56)),)),
    ), {'E8': SHARES, 'E20': AVALANCHE_SHARES}, {})
    linked = link_grove_jaaa_avalanche(history)
    assert linked == link_grove_jaaa_avalanche(linked)
    replay = replay_history(linked, DAY, DAY)
    assert replay.ledger.account(SHARES).borrowed == 50
    assert replay.ledger.account(AVALANCHE_SHARES).borrowed == 50
    assert replay.ledger.account(AVALANCHE_SHARES).borrowed_by_ilk == {'A': D(50)}
    assert replay.ledger.realised_principal_loss == 0
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
    pending = link_grove_jaaa_avalanche(replace(history, batches=history.batches[:2]))
    early = replay_history(pending, DAY, DAY)
    assert early.ledger.account(pending.custody_accounts['E8'][0]).borrowed == 50
    assert early.ledger.account(AVALANCHE_SHARES).borrowed == 0
    with pytest.raises(ValueError, match='lacks its source'):
        link_grove_jaaa_avalanche(replace(history, batches=(history.batches[2],)))
    with pytest.raises(ValueError, match='destination mismatch'):
        link_grove_jaaa_avalanche(replace(history, batches=(*history.batches[:2],
            replace(history.batches[2], timestamp=1))))


def test_explicit_corridor_links_agree_with_canonical_raw_events():
    rows = json.loads((Path(__file__).parents[1] / 'fixtures/grove_initial_custody_events.json').read_text())
    previous_delivery = 0
    for bo, txo, bi, txi in JAAA_TRANSFERS:
        sends = [r for r in rows if r['transaction_hash'] == txo and
                 r['topic0'] == '0xa16fb530429730804c0ab63e8deb338ca454bd82677d828b46231ccd1ee8c7b2']
        issues = [r for r in rows if r['transaction_hash'] == txi and
                  r['topic0'] == '0x476adf544a2827f459dee608563551a544218309c9f73a5b67d1d6c866397e16']
        assert len(sends) == len(issues) == 1
        s, r = sends[0], issues[0]
        assert s['chain'] == 'ethereum' and s['block_number'] == bo
        assert r['chain'] == 'avalanche_c' and r['block_number'] == bi
        assert s['address'] == '0xd30da1d7f964e5f6c2d9fe2aaa97517f6b23fa2b'
        assert r['address'] == '0xbcc8d02d409e439d98453c0b1ffa398dffb31fda'
        assert s['topic1'] == r['topic1'] == '0x' + f'{281474976710663:064x}'
        assert s['topic2'] == r['topic2'] == '0x0001000000000007000000000000000100000000000000000000000000000000'
        assert s['topic3'][-40:] == SHARES.split(':')[1][2:]
        sw = [int(s['data'][i:i+64], 16) for i in range(2, len(s['data']), 64)]
        rw = [int(r['data'][i:i+64], 16) for i in range(2, len(r['data']), 64)]
        assert sw[0] == 5  # Centrifuge's Avalanche domain.
        assert sw[1] == rw[0] == int(AVALANCHE_SHARES.split(':')[1], 16)
        assert sw[2] == rw[2] == 50_000_000 * 10**6
        assert s['block_time'] < r['block_time']
        assert previous_delivery < s['block_time']
        previous_delivery = r['block_time']
        for tx, account, sender in [(txo, SHARES, SHARES.split(':')[1]),
                                    (txi, AVALANCHE_SHARES, '0x' + '0' * 40)]:
            transfers = [x for x in rows if x['transaction_hash'] == tx
                         and x['address'] == account.split(':')[2]
                         and x['topic0'] == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
                         and x['topic1'][-40:] == sender[2:]]
            assert len(transfers) == 1
            assert int(transfers[0]['data'], 16) == 50_000_000 * 10**6
            recipient = s['address'] if tx == txo else AVALANCHE_SHARES.split(':')[1]
            assert transfers[0]['topic2'][-40:] == recipient[2:]
