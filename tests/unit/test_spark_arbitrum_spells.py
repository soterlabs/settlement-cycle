import gzip
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_arbitrum_spells import ILK, ROUTES, link_spark_arbitrum_spells
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

F = json.loads(gzip.decompress((Path(__file__).parents[1] / 'fixtures/spark_arbitrum_spells.json.gz').read_bytes()))
SUBPROXY = '3300f198988e4c9c63f75df86de36421f06af8c4'


def topic(s):
    return '0x' + keccak256(s.encode()).hex()


def integer(n):
    return n.to_bytes((n.bit_length() + 7) // 8, 'big')


def rlp(x):
    if isinstance(x, list):
        raw, offset = b''.join(rlp(i) for i in x), 192
    else:
        raw, offset = x, 128
        if len(raw) == 1 and raw[0] < 128:
            return raw
    if len(raw) <= 55:
        return bytes([offset + len(raw)]) + raw
    size = integer(len(raw))
    return bytes([offset + 55 + len(size)]) + size + raw


def test_arbitrum_submission_hash_redeem_schedule_and_mint_prove_all_three_deliveries():
    # Reconstruct Nitro's 0x69 typed transaction, using the canonical algorithm:
    # https://github.com/OffchainLabs/arbitrum-sdk/blob/cbb96c6f7f84d71bdef65d0fd9d3d7275a236711/packages/sdk/src/lib/message/ParentToChildMessage.ts
    actual = set()
    for item in F['arbitrum']:
        inbox, bridge = item['inbox_log'], item['bridge_log']
        source = next(s for s in F['sources'] if s['transactionHash'] == item['source'])
        assert inbox in source['logs'] and bridge in source['logs'] and source['status'] == '0x1'
        assert inbox['address'] == '0x4dbd4fc535ac27206064b68ffcf827b0a60bab3f'
        assert bridge['address'] == '0x8315177ab297ba92a06054ce80a67ed4dbd7ed3a'
        assert inbox['topics'][0] == topic('InboxMessageDelivered(uint256,bytes)')
        assert bridge['topics'][0] == topic('MessageDelivered(uint256,bytes32,address,uint8,address,bytes32,uint256,uint64)')
        assert bridge['topics'][1] == inbox['topics'][1]
        raw = bytes.fromhex(inbox['data'][2:])
        assert int.from_bytes(raw[:32]) == 32
        payload = raw[64:64 + int.from_bytes(raw[32:64])]
        words = [payload[i*32:(i+1)*32] for i in range(9)]
        nums = [int.from_bytes(w) for w in words]
        delivery = bytes.fromhex(bridge['data'][2:])
        assert delivery[:32][-20:].hex() == inbox['address'][2:]
        assert int.from_bytes(delivery[32:64]) == 9
        assert delivery[64:96][-20:].hex() == '95ca700e28b23f873b82c1beb23d86c091b618af'
        assert delivery[96:128] == keccak256(payload)
        assert words[0][-20:].hex() == '13f7f24ca959359a4d710d32c715d4bce273c793'
        call = payload[288:]
        assert len(call) == nums[8] and call[:4] == keccak256(b'finalizeInboundTransfer(address,address,address,uint256,bytes)')[:4]
        fields = [integer(42161), bytes.fromhex(inbox['topics'][1][2:]), delivery[64:96][-20:],
                  integer(int.from_bytes(delivery[128:160])), integer(nums[2]), integer(nums[7]),
                  integer(nums[6]), words[0][-20:], integer(nums[1]), words[5][-20:],
                  integer(nums[3]), words[4][-20:], call]
        ticket = '0x' + keccak256(b'\x69' + rlp(fields)).hex()
        creation, final = item['ticket_receipt'], item['delivery_receipt']
        assert ticket == creation['transactionHash'] == item['ticket']
        assert creation['status'] == final['status'] == '0x1'
        event = next(row for row in creation['logs'] if row['topics'][0] == topic('RedeemScheduled(bytes32,bytes32,uint64,uint64,address,uint256,uint256)'))
        assert event['address'] == '0x000000000000000000000000000000000000006e'
        assert event['topics'][1] == ticket and event['topics'][2] == final['transactionHash']
        args = call[4:]
        origin, sender, holder = [args[i*32:(i+1)*32][-20:].hex() for i in range(3)]
        units = int.from_bytes(args[96:128])
        assert sender == SUBPROXY and holder == '92afd6f2385a90e44da3a8b60fe36f6cbe1d8709'
        assert any(row['address'] == '0x' + origin and row['topics'][0] == TRANSFER_TOPIC0
                   and row['topics'][1].endswith(sender) and row['topics'][2].endswith('a10c7ce4b876998858b1a9e12b10092229539400')
                   and int(row['data'], 16) == units for row in source['logs'])
        route = next(leg for _, _, _, _, legs in ROUTES for leg in legs if leg[1] == final['transactionHash'])
        mint = next(row for row in final['logs'] if row['topics'][0] == TRANSFER_TOPIC0)
        assert mint['address'] == route[4].split(':')[2] and int(mint['topics'][1], 16) == 0
        assert mint['topics'][2].endswith(holder) and int(mint['data'], 16) == units
        assert int(final['blockNumber'], 16) == route[2]
        actual.add(final['transactionHash'])
    assert actual == {leg[1] for *_, legs in ROUTES for leg in legs if leg[0] == 'arbitrum'}


def test_companion_op_stack_relays_and_actual_source_draws():
    seen = set()
    for source, (_, block, _, draw, legs) in zip(F['sources'], ROUTES, strict=True):
        assert source['status'] == '0x1' and int(source['blockNumber'], 16) == block
        transfers = [row for row in source['logs'] if row['topics'][0] == TRANSFER_TOPIC0]
        assert any(row['address'] == '0xdc035d45d973e3ec169d2276ddab16f1e407384f'
                   and row['topics'][1].endswith('c395d150e71378b47a1b8e9de0c1a83b75a08324')
                   and row['topics'][2].endswith(SUBPROXY) and int(row['data'], 16) == int(draw * 10**18) for row in transfers)
        wrapped = draw - (D('100000000') if draw == D('300000000') else D(0))
        assert any(row['address'] == '0xdc035d45d973e3ec169d2276ddab16f1e407384f'
                   and row['topics'][1].endswith(SUBPROXY) and row['topics'][2].endswith('a3931d71877c0e7a3148cb7eb4463524fec27fbd')
                   and int(row['data'], 16) == int(wrapped * 10**18) for row in transfers)
        leg = next(leg for leg in legs if leg[0] != 'arbitrum')
        dest = next(x['receipt'] for x in F['op_stack'] if x['chain'] == leg[0])
        for log in source['logs']:
            if log['topics'][0] != topic('SentMessage(address,address,bytes,uint256,uint256)'):
                continue
            raw = bytes.fromhex(log['data'][2:])
            off = int.from_bytes(raw[32:64])
            call = raw[off+32:off+32+int.from_bytes(raw[off:off+32])]
            if call[:4] != keccak256(b'finalizeBridgeERC20(address,address,address,address,uint256,bytes)')[:4]:
                continue
            messenger, sender, target, escrow = (
                ('866e82a600a1414e583f7f13623f1ac5d58b0afa', 'a5874756416fa632257eea380cabd2e87ced352a', 'ee44cdb68d618d58f75d9fe0818b640bd7b8a7b7', '7f311a4d48377030bd810395f4ccfc03bdbe9ef3') if leg[0] == 'base' else
                ('25ace71c97b33cc4729cf772ae268934f7ab5fa1', '3d25b7d486cae1810374d37a48bcf0963c9b8057', '8f41dbf6b8498561ce1d73af16cd9c0d8ee20ba6', '467194771dae2967aef3ecbedd3bf9a310c76c65'))
            assert log['address'] == '0x' + messenger and raw[:32][-20:].hex() == sender and log['topics'][1].endswith(target)
            ext = next(x for x in source['logs'] if int(x['logIndex'], 16) == int(log['logIndex'], 16) + 1)
            assert ext['address'] == log['address'] and ext['topics'][0] == topic('SentMessageExtension1(address,uint256)')
            assert ext['topics'][1].endswith(sender) and int(ext['data'], 16) == 0
            encoded = keccak256(b'relayMessage(uint256,address,address,uint256,uint256,bytes)')[:4]
            encoded += raw[64:96] + raw[:32] + bytes.fromhex(log['topics'][1][2:]) + bytes(32) + raw[96:128] + (192).to_bytes(32)
            encoded += len(call).to_bytes(32) + call + bytes((-len(call)) % 32)
            hash_ = '0x' + keccak256(encoded).hex()
            assert any(row['address'] == '0x4200000000000000000000000000000000000007'
                       and row['topics'] == [topic('RelayedMessage(bytes32)'), hash_] for row in dest['logs'])
            args = call[4:]
            token, origin, frm, to = [args[i*32:(i+1)*32][-20:].hex() for i in range(4)]
            units = int.from_bytes(args[128:160])
            assert frm == SUBPROXY and leg[4] == f'{leg[0]}:0x{to}:0x{token}'
            assert any(row['address'] == '0x' + origin and row['topics'][1].endswith(frm)
                       and row['topics'][2].endswith(escrow) and int(row['data'], 16) == units for row in transfers)
            assert any(row['address'] == '0x' + token and row['topics'][0] == TRANSFER_TOPIC0
                       and int(row['topics'][1], 16) == 0 and row['topics'][2].endswith(to)
                       and int(row['data'], 16) == units for row in dest['logs'])
            assert dest['transactionHash'] == leg[1] and dest['status'] == '0x1'
            assert int(dest['blockNumber'], 16) == leg[2]
            seen.add(leg[1])
    assert len(seen) == 2


def history():
    batches = []
    for row in F['normalized_batches']:
        b = dict(row)
        b['day'] = date.fromisoformat(b['day'])
        b['minted'] = D(b['minted'])
        b['minted_by_ilk'] = {k: D(v) for k, v in b['minted_by_ilk'].items()}
        b['movements'] = tuple(AssetMovement(m['account'], D(m['value_before']), D(m['change']), D(m['external_income']), m['preserve_basis']) for m in b['movements'])
        batches.append(CapitalBatch(**b))
    return CapitalHistory(tuple(batches), {}, {})


def test_paid_principal_survives_both_routes_without_financing_gifts_or_savings_growth():
    for _, _, _, draw, legs in ROUTES:
        h = history()
        day = next(b.day for b in h.batches if abs(b.minted - draw) < D('1e-8'))
        h = replace(h, batches=tuple(b for b in h.batches if b.day == day))
        linked = link_spark_arbitrum_spells(h)
        assert link_spark_arbitrum_spells(linked) is linked
        r = replay_history(linked, day, day)
        assert not r.unmatched_receipts and not r.unmatched_outflows
        assert abs(r.ledger.drawn_by_ilk[ILK] - draw) < D('1e-8')
        for *_, account, cost, _ in legs:
            assert abs(r.ledger.account(account).borrowed - cost) < D('1e-8')
        for m in next(b for b in h.batches if b.chain == 'ethereum').movements:
            assert r.ledger.account(m.account).borrowed == 0
        assert not linked.idle_accounts
        assert sum(b.minted for b in linked.batches) == sum(b.minted for b in h.batches)


def test_cutoff_missing_funding_unrelated_primes_and_conflicting_events():
    h = history()
    first = h.batches[0]
    cutoff = replace(h, batches=(first,))
    r = replay_history(link_spark_arbitrum_spells(cutoff), first.day, first.day)
    assert not r.unmatched_outflows
    assert abs(sum(r.daily[first.day].values()) - first.minted) < D('1e-8')
    unfunded = replace(h, batches=tuple(b for b in h.batches if b.chain != 'ethereum'))
    assert link_spark_arbitrum_spells(unfunded) is unfunded
    unrelated = CapitalHistory((replace(first, minted=D(0), minted_by_ilk={}),), {'E1': 'grove'}, {})
    assert link_spark_arbitrum_spells(unrelated) is unrelated
    with pytest.raises(ValueError, match='draw or existing'):
        link_spark_arbitrum_spells(replace(h, batches=(replace(first, minted=D(1)), *h.batches[1:])))
    with pytest.raises(ValueError, match='receipt differs'):
        link_spark_arbitrum_spells(replace(h, batches=(first, replace(h.batches[1], block=1), *h.batches[2:])))
    linked = link_spark_arbitrum_spells(h)
    with pytest.raises(ValueError, match='append raw'):
        link_spark_arbitrum_spells(replace(linked, batches=(*linked.batches, h.batches[1])))
