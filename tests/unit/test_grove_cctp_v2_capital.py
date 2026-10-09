import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_cctp_v2_capital import (
    HOLDERS,
    ROUTES,
    TOKENS,
    cash_account,
    link_grove_cctp_v2,
)
from settle.extract._keccak import keccak256
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

PROOFS = json.loads((Path(__file__).parents[1] / 'fixtures/grove_cctp_v2_proofs.json').read_text())
TRANSMITTER = '0x81d40f21f12a8f0e3252bccb954d722d4c464b64'
MESSENGER = '0x28b5a0e9c621a5badaa536219b3a228c8168cf5d'
MINTER = '0xfd78ee919681417d192449715b2594ab58f5d002'
DOMAINS = {'ethereum': 0, 'base': 6}


def topic(signature):
    return '0x' + keccak256(signature.encode()).hex()


def dynamic(row, word):
    data = bytes.fromhex(row['data'][2:])
    offset = int.from_bytes(data[word * 32:(word + 1) * 32], 'big')
    size = int.from_bytes(data[offset:offset + 32], 'big')
    result = data[offset + 32:offset + 32 + size]
    assert len(result) == size
    return result


def test_every_route_has_funded_source_message_iris_nonce_and_authenticated_mint():
    assert len(PROOFS) == len(ROUTES) == 77
    consumed_payments, nonces = set(), set()
    for route, p in zip(ROUTES, PROOFS, strict=True):
        source, sent, source_block, dest, received, dest_block, nonce, amount = route
        assert (source, sent, source_block, dest, received, dest_block, nonce, amount) == (
            p['source_chain'], p['source_transaction'], p['source_block'],
            p['destination_chain'], p['destination_transaction'], p['destination_block'],
            p['nonce'], D(p['amount']))
        assert nonce not in nonces
        nonces.add(nonce)
        assert p['iris_url'] == f'https://iris-api.circle.com/v2/messages/{DOMAINS[source]}?transactionHash={sent}'
        assert p['iris']['status'] == 'complete' and p['iris']['cctpVersion'] == 2
        message = bytes.fromhex(p['iris']['message'][2:])
        assert len(message) == 376 and message[:4] == bytes.fromhex('00000001')
        assert int.from_bytes(message[4:8], 'big') == DOMAINS[source]
        assert int.from_bytes(message[8:12], 'big') == DOMAINS[dest]
        assert '0x' + message[12:44].hex() == nonce == p['iris']['eventNonce']
        assert '0x' + message[56:76].hex() == MESSENGER
        assert '0x' + message[88:108].hex() == MESSENGER
        assert not any(message[108:140])  # No restricted destination caller.
        assert int.from_bytes(message[140:144], 'big') == 2000
        assert int.from_bytes(message[144:148], 'big') == 2000
        body = message[148:]
        assert body[:4] == bytes.fromhex('00000001')
        assert '0x' + body[16:36].hex() == TOKENS[source]
        assert '0x' + body[48:68].hex() == HOLDERS[dest]
        assert D(int.from_bytes(body[68:100], 'big')) / 10**6 == amount
        assert '0x' + body[112:132].hex() == HOLDERS[source]
        assert not any(body[132:])  # No max fee, executed fee, expiration or hook.
        source_log = next(r for r in p['source_logs'] if r['log_index'] == p['source_log'])
        assert source_log['address'] == TRANSMITTER
        assert source_log['topic0'] == topic('MessageSent(bytes)')
        assert source_log['transaction_hash'] == sent and source_log['block_number'] == source_block
        template = bytearray(message)
        template[12:44], template[144:148] = bytes(32), bytes(4)
        assert dynamic(source_log, 0) == template
        burn = next(r for r in p['source_logs'] if r['address'] == MESSENGER
            and r['topic0'] == topic('DepositForBurn(address,uint256,address,bytes32,uint32,bytes32,bytes32,uint256,uint32,bytes)')
            and r['log_index'] == source_log['log_index'] + 1)
        assert burn['topic1'][-40:] == TOKENS[source][2:]
        assert burn['topic2'][-40:] == HOLDERS[source][2:]
        assert int(burn['topic3'], 16) == 2000
        words = bytes.fromhex(burn['data'][2:])
        assert D(int.from_bytes(words[:32], 'big')) / 10**6 == amount
        assert '0x' + words[44:64].hex() == HOLDERS[dest]
        assert int.from_bytes(words[64:96], 'big') == DOMAINS[dest]
        assert '0x' + words[108:128].hex() == MESSENGER
        # Consume actual USDC payment and subsequent burn. Equal-sized messages
        # cannot reuse one payment; a transaction can contain multiple messages.
        payments = [r for r in p['source_logs'] if r['address'] == TOKENS[source]
            and r['topic0'] == topic('Transfer(address,address,uint256)')
            and r['topic1'][-40:] == HOLDERS[source][2:] and r['topic2'][-40:] == MINTER[2:]
            and D(int(r['data'], 16)) / 10**6 == amount and r['log_index'] < source_log['log_index']]
        payment = max(payments, key=lambda r: r['log_index'])
        key = (source, sent, payment['log_index'])
        assert key not in consumed_payments
        consumed_payments.add(key)
        assert any(r['address'] == TOKENS[source] and r['topic0'] == topic('Transfer(address,address,uint256)')
            and r['topic1'][-40:] == MINTER[2:] and int(r['topic2'], 16) == 0
            and r['data'] == payment['data'] and payment['log_index'] < r['log_index'] < source_log['log_index']
            for r in p['source_logs'])
        receipt = next(r for r in p['destination_logs'] if r['address'] == TRANSMITTER
            and r['topic0'] == topic('MessageReceived(address,uint32,bytes32,bytes32,uint32,bytes)')
            and r['topic2'] == nonce)
        assert receipt['transaction_hash'] == received and receipt['block_number'] == dest_block
        raw = bytes.fromhex(receipt['data'][2:])
        assert int.from_bytes(raw[:32], 'big') == DOMAINS[source]
        assert '0x' + raw[44:64].hex() == MESSENGER
        assert int(receipt['topic3'], 16) == 2000 and dynamic(receipt, 2) == body
        mint = next(r for r in p['destination_logs'] if r['address'] == MESSENGER
            and r['topic0'] == topic('MintAndWithdraw(address,uint256,address,uint256)'))
        assert mint['topic1'][-40:] == HOLDERS[dest][2:] and mint['topic2'][-40:] == TOKENS[dest][2:]
        assert D(int(mint['data'][2:66], 16)) / 10**6 == amount and int(mint['data'][66:], 16) == 0
        assert any(r['address'] == TOKENS[dest] and r['topic0'] == topic('Transfer(address,address,uint256)')
            and int(r['topic1'], 16) == 0 and r['topic2'][-40:] == HOLDERS[dest][2:]
            and D(int(r['data'], 16)) / 10**6 == amount for r in p['destination_logs'])
    assert sum(r[-1] for r in ROUTES) == D('348603786.874274')


def history(selected):
    batches = {}
    for route in selected:
        source, sent, sb, dest, received, db, nonce, amount = route
        p = next(p for p in PROOFS if p['nonce'] == nonce)
        for outgoing, chain, tx, block, logs in ((True, source, sent, sb, p['source_logs']),
                                               (False, dest, received, db, p['destination_logs'])):
            ts = logs[0]['block_time']
            day = datetime.fromtimestamp(ts, UTC).date()
            identity = f'{chain}:{tx}'
            old = batches.get(identity)
            change = (-amount if outgoing else amount) + (old.movements[0].change if old else D(0))
            batches[identity] = CapitalBatch(identity, day, ts, chain, block,
                (AssetMovement(cash_account(chain), -change if outgoing else D(0), change),))
    source = selected[0][0]
    first = min(batches.values(), key=lambda b: b.timestamp)
    total = sum(r[-1] for r in selected)
    funding = replace(first, identity='funding', timestamp=first.timestamp - 1,
        movements=(AssetMovement(cash_account(source), D(0), total),), minted=total)
    balances = {cash_account(source): total}
    ordered = []
    for b in sorted(batches.values(), key=lambda b: b.timestamp):
        movement = b.movements[0]
        before = balances.get(movement.account, D(0))
        ordered.append(replace(b, movements=(replace(movement, value_before=before),)))
        balances[movement.account] = before + movement.change
    return CapitalHistory((funding, *ordered),
        {chain: cash_account(chain) for chain in HOLDERS}, {})


@pytest.mark.parametrize('route', ROUTES)
def test_each_authenticated_transfer_preserves_basis_without_new_debt(route):
    selected = [r for r in ROUTES if r[0:2] == route[0:2]]
    h = history(selected)
    linked = link_grove_cctp_v2(h)
    assert linked == link_grove_cctp_v2(linked)
    r = replay_history(h, min(b.day for b in h.batches), max(b.day for b in h.batches))
    assert r.ledger.account(cash_account(route[3])).borrowed == sum(x[-1] for x in selected)
    assert r.ledger.realised_principal_loss == 0
    assert not r.unmatched_receipts and not r.unmatched_outflows


def test_multiple_messages_share_one_source_payment_batch():
    group = [r for r in ROUTES if r[1] == '0x8d35d1618ce8125eca0b1c60321962953dd9bb7d8900063da96b8daccaa618f6']
    assert len(group) == 2
    h = history(group)
    r = replay_history(h, min(b.day for b in h.batches), max(b.day for b in h.batches))
    assert r.ledger.account(cash_account('base')).borrowed == D('15000000')
    assert r.ledger.account(cash_account('base')).value == D('15000000')
    assert not r.unmatched_receipts and not r.unmatched_outflows


def test_pin_before_receipt_retains_borrowed_and_earned_cash_in_transit():
    route = ROUTES[0]
    h = history([route])
    first = h.batches[0]
    borrowed, earned = route[-1] * D('.8'), route[-1] * D('.2')
    h = replace(h, batches=(replace(first, minted=borrowed, movements=(
        replace(first.movements[0], external_income=earned),)), h.batches[1]))
    linked = link_grove_cctp_v2(h)
    r = replay_history(h, min(b.day for b in h.batches), max(b.day for b in h.batches))
    claim = linked.custody_accounts[route[0]][0]
    assert r.ledger.account(claim).borrowed == borrowed
    assert r.ledger.account(claim).value == borrowed + earned


@pytest.mark.parametrize('fault', ['source_missing', 'wrong_block', 'wrong_amount', 'minted', 'append_raw'])
def test_changed_route_cannot_invent_funding_or_consume_it_twice(fault):
    h = history([ROUTES[0]])
    if fault == 'source_missing':
        h = replace(h, batches=(h.batches[-1],))
    elif fault == 'append_raw':
        linked = link_grove_cctp_v2(h)
        h = replace(linked, batches=(*linked.batches, h.batches[-1]))
    else:
        last = h.batches[-1]
        if fault == 'wrong_block':
            last = replace(last, block=1)
        elif fault == 'wrong_amount':
            last = replace(last, movements=(replace(last.movements[0], change=D(1)),))
        else:
            last = replace(last, minted=D(1))
        h = replace(h, batches=(*h.batches[:-1], last))
    with pytest.raises(ValueError, match='Grove CCTP v2'):
        link_grove_cctp_v2(h)
