import gzip
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_early_base_seed import HOLDER, ILK, ROUTES, link_spark_early_base_seed
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory


def topic(s):
    return '0x' + keccak256(s.encode()).hex()


def test_exact_spells_cash_and_canonical_messages_prove_base_delivery():
    f = json.loads(gzip.decompress((Path(__file__).parents[1] / 'fixtures/spark_early_base_seed.json.gz').read_bytes()))
    subproxy = '3300f198988e4c9c63f75df86de36421f06af8c4'
    usds = '0xdc035d45d973e3ec169d2276ddab16f1e407384f'
    susds = '0xa3931d71877c0e7a3148cb7eb4463524fec27fbd'
    escrow = '7f311a4d48377030bd810395f4ccfc03bdbe9ef3'
    decoded = set()
    for tx, block, _, draw, legs in ROUTES:
        rows = next(s['rows'] for s in f['sources'] if s['control']['identity'] == 'ethereum:' + tx)
        assert all(r['transaction_hash'] == tx and r['block_number'] == block for r in rows)
        transfers = [r for r in rows if r['topic0'] == TRANSFER_TOPIC0]
        assert any(r['address'] == usds and r['topic1'].endswith('c395d150e71378b47a1b8e9de0c1a83b75a08324')
            and r['topic2'].endswith(subproxy) and int(r['data'], 16) == int(draw * 10**18) for r in transfers)
        wrapped = sum((leg[4] for leg in legs if leg[3].startswith('0x5875')), D(0))
        if wrapped:
            assert any(r['address'] == usds and r['topic1'].endswith(subproxy)
                and r['topic2'].endswith(susds[2:]) and int(r['data'], 16) == int(wrapped * 10**18) for r in transfers)
        for r in rows:
            if r['topic0'] != topic('SentMessage(address,address,bytes,uint256,uint256)'):
                continue
            body = bytes.fromhex(r['data'][2:])
            off = int.from_bytes(body[32:64])
            size = int.from_bytes(body[off:off+32])
            payload = body[off+32:off+32+size]
            if payload[:4] != keccak256(b'finalizeBridgeERC20(address,address,address,address,uint256,bytes)')[:4]:
                continue
            # Base's canonical messenger relays Sky's token bridge, whose
            # escrow is a distinct address holding the actual L1 assets.
            assert r['address'] == '0x866e82a600a1414e583f7f13623f1ac5d58b0afa'
            assert body[:32][-20:].hex() == 'a5874756416fa632257eea380cabd2e87ced352a'
            assert r['topic1'].endswith('ee44cdb68d618d58f75d9fe0818b640bd7b8a7b7')
            args = payload[4:]
            token, origin, sender, holder = ['0x' + args[i*32:(i+1)*32][-20:].hex() for i in range(4)]
            amount = int.from_bytes(args[128:160])
            assert sender == '0x' + subproxy and holder == HOLDER
            assert any(t['address'] == origin and t['topic1'].endswith(subproxy)
                and t['topic2'].endswith(escrow) and int(t['data'], 16) == amount for t in transfers)
            ext = next(x for x in rows if x['log_index'] == r['log_index'] + 1)
            assert ext['address'] == r['address'] and ext['topic0'] == topic('SentMessageExtension1(address,uint256)')
            assert ext['topic1'].endswith('a5874756416fa632257eea380cabd2e87ced352a') and int(ext['data'], 16) == 0
            encoded = keccak256(b'relayMessage(uint256,address,address,uint256,uint256,bytes)')[:4]
            encoded += body[64:96] + body[:32] + bytes.fromhex(r['topic1'][2:]) + bytes(32) + body[96:128] + (192).to_bytes(32)
            encoded += len(payload).to_bytes(32) + payload + bytes((-len(payload)) % 32)
            hash_ = '0x' + keccak256(encoded).hex()
            relay = next(x for x in f['base'] if x['topic0'] == topic('RelayedMessage(bytes32)') and x['topic1'] == hash_)
            assert relay['address'] == '0x4200000000000000000000000000000000000007'
            mint = next(x for x in f['base'] if x['transaction_hash'] == relay['transaction_hash'] and x['topic0'] == TRANSFER_TOPIC0)
            assert mint['address'] == token and int(mint['topic1'], 16) == 0
            assert mint['topic2'].endswith(holder[2:]) and int(mint['data'], 16) == amount
            leg = next(leg for leg in legs if leg[0] == relay['transaction_hash'])
            assert (mint['block_number'], mint['block_time'], mint['address']) == leg[1:4]
            decoded.add((tx, leg[0]))
    assert decoded == {(tx, leg[0]) for tx, _, _, _, legs in ROUTES for leg in legs}


def history():
    batches = []
    for tx, block, stamp, draw, legs in ROUTES:
        day = datetime.fromtimestamp(stamp, UTC).date()
        batches.append(CapitalBatch('ethereum:' + tx, day, stamp, 'ethereum', block, (), draw,
            minted_by_ilk={ILK: draw}))
        for dest, db, ds, token, _, value in legs:
            batches.append(CapitalBatch('base:' + dest, datetime.fromtimestamp(ds, UTC).date(), ds,
                'base', db, (AssetMovement(f'base:{HOLDER}:{token}', D(0), value),)))
    return CapitalHistory(tuple(batches), {}, {})


def test_paid_cost_not_savings_accretion_is_carried_across_bridge():
    # Replay each initial funding group in isolation: real intervening spending
    # is deliberately not synthesized between these three historical spells.
    for tx, _, _, draw, legs in ROUTES:
        h = history()
        ids = {'ethereum:' + tx, *('base:' + leg[0] for leg in legs)}
        h = replace(h, batches=tuple(b for b in h.batches if b.identity in ids))
        linked = link_spark_early_base_seed(h)
        assert linked == link_spark_early_base_seed(linked)
        day = h.batches[-1].day
        r = replay_history(linked, day, day)
        assert not r.unmatched_receipts and not r.unmatched_outflows
        assert r.ledger.drawn_by_ilk == {ILK: draw}
        for _, _, _, token, cost, _ in legs:
            assert r.ledger.account(f'base:{HOLDER}:{token}').borrowed == cost
        assert sum(r.daily[day].values()) == draw
        assert not linked.idle_accounts


def test_cutoff_unknown_funding_and_conflicting_input():
    h = history()
    source = h.batches[0]
    cutoff = replace(h, batches=(source,))
    r = replay_history(link_spark_early_base_seed(cutoff), source.day, source.day)
    assert sum(r.daily[source.day].values()) == D('9000000')
    assert not r.unmatched_outflows
    receipts = replace(h, batches=h.batches[1:3])
    assert link_spark_early_base_seed(receipts) == receipts
    r = replay_history(receipts, source.day, source.day)
    assert len(r.unmatched_receipts) == 2
    with pytest.raises(ValueError, match='existing custody'):
        link_spark_early_base_seed(replace(h, batches=(replace(source,
            movements=(AssetMovement('existing', D(0), D(1)),)), *h.batches[1:])))
    linked = link_spark_early_base_seed(h)
    with pytest.raises(ValueError, match='append raw'):
        link_spark_early_base_seed(replace(linked, batches=(*linked.batches, h.batches[1])))
    wrong = replace(h.batches[1], block=1)
    with pytest.raises(ValueError, match='receipt differs'):
        link_spark_early_base_seed(replace(h, batches=(source, wrong, *h.batches[2:])))
