import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_apollo_cash_settlements import (
    CASH,
    GROUPS,
    SOURCE,
    link_grove_apollo_cash_settlements,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

RAW = json.loads((Path(__file__).parents[1] / 'fixtures/grove_apollo_cash_settlement_events.json').read_text())
HOLDER = '1db91ad50446a671e2231f77e00948e68876f812'
TOKEN = '0x9477724bb54ad5417de8baff29e59df3fb4da74f'
TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
REVOKE = '0xf29c0bb0c776b9a8c0d95e1767a339d75d062925109fbcdde8f4ace03a5fbfad'


def row(chain, tx):
    return next(r for r in RAW[chain] if r['transaction_hash'] == tx)


def history(group):
    receipts, burns, held = group
    first = row('ethereum', receipts[0][0])
    day = datetime.fromtimestamp(first['block_time'], UTC).date()
    units = D(held) / 10**18
    batches = [CapitalBatch('funding', day, first['block_time'] - 1, 'plume', 1,
        (AssetMovement(SOURCE, D(0), units),), units)]
    cash = D(0)
    for tx, block, amount in receipts:
        r = row('ethereum', tx)
        batches.append(CapitalBatch('ethereum:' + tx,
            datetime.fromtimestamp(r['block_time'], UTC).date(), r['block_time'],
            'ethereum', block, (AssetMovement(CASH, cash, amount),)))
        cash += amount
    for tx, block, shares in burns:
        r = row('plume', tx)
        price_row = next(r for r in RAW['plume'] if r['transaction_hash'] == tx and r['topic0'] == REVOKE)
        price = D(int(price_row['data'][130:194], 16)) / 10**18
        batches.append(CapitalBatch('plume:' + tx,
            datetime.fromtimestamp(r['block_time'], UTC).date(), r['block_time'],
            'plume', block, (AssetMovement(SOURCE, D(held) / 10**18 * price, -D(shares) / 10**18 * price),)))
        held -= shares
    return CapitalHistory(tuple(batches), {'Apollo': SOURCE, 'Cash': CASH}, {})


def test_reviewed_cash_totals_match_canonical_share_cancellations_at_emitted_price():
    balance = 0
    held_before = {}
    for r in RAW['plume']:
        if r['address'] != TOKEN or r['topic0'] != TRANSFER:
            continue
        if r['topic2'].endswith(HOLDER):
            balance += int(r['data'], 16)
        if r['topic1'].endswith(HOLDER):
            held_before[r['transaction_hash']] = balance
            balance -= int(r['data'], 16)
    total = D(0)
    for receipts, burns, held in GROUPS:
        cash = D(0)
        for tx, block, amount in receipts:
            r = row('ethereum', tx)
            assert r['block_number'] == block
            assert r['address'] == '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
            assert r['topic1'][-40:] in ('cd531ae9efcce479654c4926dec5f6209531ca7b',
                                         'a9d1e08c7793af67e9d92fe308d5697fb81d3e43')
            assert r['topic2'][-40:] == '491edfb0b8b608044e227225c715981a30f3a44e'
            assert D(int(r['data'], 16)) / 10**6 == amount
            cash += amount
        cancelled = D(0)
        for tx, block, shares in burns:
            assert held == held_before[tx]
            r = row('plume', tx)
            assert r['block_number'] == block and r['address'] == TOKEN
            assert r['topic1'].endswith(HOLDER)
            assert r['topic2'].endswith('8ef19b8cee9dfdce42ffbc405ba828dc6e920c3c')
            assert int(r['data'], 16) == shares
            price_row = next(r for r in RAW['plume'] if r['transaction_hash'] == tx and r['topic0'] == REVOKE)
            assert price_row['address'] == '0x12a110ce5f0fc871cc72bc7ecaf35cf39dd0f43e'
            assert int(price_row['data'][194:258], 16) == shares
            price = D(int(price_row['data'][130:194], 16)) / 10**18
            cancelled += price * D(shares) / 10**18
            assert r['block_time'] > row('ethereum', receipts[-1][0])['block_time']
            held -= shares
        # Issuer cash is rounded to cents; this history agrees within one micro-USDC.
        assert abs(cash - cancelled) < D('1e-6')
        total += cash
    assert total == D('30341382.77')


@pytest.mark.parametrize('group', GROUPS)
def test_basis_moves_on_cash_date_and_cannot_move_again_on_token_cancellation(group):
    h = history(group)
    linked = link_grove_apollo_cash_settlements(h)
    assert linked == link_grove_apollo_cash_settlements(linked)
    r = replay_history(h, min(b.day for b in h.batches), max(b.day for b in h.batches))
    redeemed = D(sum(s for _, _, s in group[1])) / 10**18
    remaining = D(group[2]) / 10**18 - redeemed
    assert abs(r.ledger.account(SOURCE).borrowed - remaining) < D('1e-12')
    assert abs(r.ledger.account(CASH).borrowed - redeemed) < D('1e-12')
    assert r.ledger.account(CASH).value == sum(a for _, _, a in group[0])
    assert r.ledger.realised_principal_loss == 0
    assert not r.unmatched_receipts and not r.unmatched_outflows
    cash_day = h.batches[2].day
    assert abs(r.daily[cash_day][SOURCE] - remaining) < D('1e-12')


def test_earned_funding_does_not_turn_into_borrowed_redemption_cash():
    group = GROUPS[0]
    h = history(group)
    first = h.batches[0]
    h = replace(h, batches=(replace(first, minted=first.minted * D('.8'), movements=(
        replace(first.movements[0], external_income=first.minted * D('.2')),)), *h.batches[1:]))
    r = replay_history(h, min(b.day for b in h.batches), max(b.day for b in h.batches))
    redeemed = D(sum(s for _, _, s in group[1])) / 10**18
    assert abs(r.ledger.account(CASH).borrowed - redeemed * D('.8')) < D('1e-12')


def test_incomplete_pin_remains_unresolved_without_using_future_cancellation():
    h = history(GROUPS[0])
    h = replace(h, batches=h.batches[:3])
    assert link_grove_apollo_cash_settlements(h) == h
    r = replay_history(h, min(b.day for b in h.batches), max(b.day for b in h.batches))
    assert len(r.unmatched_receipts) == 2
    assert r.ledger.account(CASH).borrowed == 0


@pytest.mark.parametrize('fault', ['changed_cash', 'changed_holdings', 'wrong_block', 'intervening_transfer', 'append_raw'])
def test_changed_or_ambiguous_settlement_fails(fault):
    h = history(GROUPS[0])
    if fault == 'changed_cash':
        b = h.batches[1]
        h = replace(h, batches=(h.batches[0], replace(b, movements=(replace(b.movements[0], change=D(2)),)), *h.batches[2:]))
    elif fault == 'changed_holdings':
        b = h.batches[-1]
        h = replace(h, batches=(*h.batches[:-1], replace(b, movements=(replace(b.movements[0], value_before=D(100)),))))
    elif fault == 'wrong_block':
        h = replace(h, batches=(*h.batches[:-1], replace(h.batches[-1], block=1)))
    elif fault == 'intervening_transfer':
        b = replace(h.batches[-1], identity='plume:unreviewed', timestamp=h.batches[1].timestamp + 1)
        h = replace(h, batches=(*h.batches, b))
    else:
        linked = link_grove_apollo_cash_settlements(h)
        h = replace(linked, batches=(*linked.batches, h.batches[-1]))
    with pytest.raises(ValueError, match=r'Apollo|append raw'):
        link_grove_apollo_cash_settlements(h)
