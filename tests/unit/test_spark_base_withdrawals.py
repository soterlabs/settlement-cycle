import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_base_withdrawals import (
    BASE_ALM,
    ETH_ALM,
    LEGS,
    SOURCE,
    link_spark_base_withdrawals,
)
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

DAY = date(2026, 7, 6)
ILK = 'sky-ilk'


def history():
    source = CapitalBatch(SOURCE, DAY, 1783361153, 'base', 48285903, tuple(
        AssetMovement(f'base:{BASE_ALM}:{local}', value, -value)
        for local, _, _, _, value, *_ in LEGS))
    funding = replace(source, identity='prior-funding', day=DAY-timedelta(days=1), timestamp=1783274753,
        minted=sum((leg[4] for leg in LEGS),D(0))/2,
        minted_by_ilk={ILK:sum((leg[4] for leg in LEGS),D(0))/2},
        movements=tuple(AssetMovement(m.account,D(0),m.value_before,external_income=m.value_before/2)
                        for m in source.movements))
    deliveries = tuple(CapitalBatch('ethereum:'+tx, date(2026,7,13), stamp, 'ethereum', block,
        (AssetMovement(f'ethereum:{ETH_ALM}:{remote}',D(0),amount),))
        for _, remote, _, tx, _, amount, block, stamp in LEGS)
    return CapitalHistory((funding,source,*deliveries), {}, {})


def test_funded_native_custody_survives_one_week_and_susds_growth_creates_no_basis():
    h = history()
    linked = link_spark_base_withdrawals(h)
    assert link_spark_base_withdrawals(linked) == linked
    r = replay_history(linked, DAY, date(2026,7,13))
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert len(linked.analytics_only_venues) == 2
    for n, (_,remote,_,_,value,*_) in enumerate(LEGS):
        claim = linked.venue_accounts[f'S_BASE_NATIVE_PENDING_{n+1}']
        assert abs(r.daily[DAY][claim]-value/2) < D('1e-18')
        assert r.daily[date(2026,7,13)][claim] == 0
        assert abs(r.ledger.account(f'ethereum:{ETH_ALM}:{remote}').borrowed-value/2) < D('1e-18')
        assert claim not in linked.idle_accounts
    assert r.ledger.realised_principal_loss == 0


def test_partial_cutoff_keeps_custody_and_wrong_receipt_fails():
    h = history()
    cutoff = replace(h,batches=h.batches[:2])
    r = replay_history(link_spark_base_withdrawals(cutoff),DAY,DAY)
    assert not r.unmatched_outflows
    assert sum(r.daily[DAY].values()) > D('179000000')
    delivery = h.batches[2]
    broken = replace(delivery,movements=(replace(delivery.movements[0],change=D(1)),))
    with pytest.raises(ValueError,match='receipt differs'):
        link_spark_base_withdrawals(replace(h,batches=(*h.batches[:2],broken,h.batches[3])))
    # A destination alone cannot fabricate the earlier source or borrowed basis.
    receipt_only = replace(h,batches=h.batches[2:])
    assert link_spark_base_withdrawals(receipt_only) == receipt_only


def topic(signature):
    return '0x'+keccak256(signature.encode()).hex()


def dynamic(data, head_index):
    offset = int.from_bytes(data[head_index*32:(head_index+1)*32])
    size = int.from_bytes(data[offset:offset+32])
    return data[offset+32:offset+32+size]


def test_canonical_payload_hashes_and_actual_burns_authenticate_l1_receipts():
    f = json.loads((Path(__file__).parents[1]/'fixtures/spark_base_july_withdrawals.json').read_text())
    messages = [r for r in f['base'] if r['address']=='0x4200000000000000000000000000000000000016']
    assert len(messages) == len(LEGS) == 2
    for r,leg in zip(messages,LEGS,strict=True):
        local,remote,raw_amount,tx,*_ = leg
        body = bytes.fromhex(r['data'][2:])
        payload = dynamic(body,2)
        assert payload[:4] == keccak256(b'relayMessage(uint256,address,address,uint256,uint256,bytes)')[:4]
        args = payload[4:]
        assert '0x'+args[32:64][-20:].hex() == '0xee44cdb68d618d58f75d9fe0818b640bd7b8a7b7'
        assert '0x'+args[64:96][-20:].hex() == '0xa5874756416fa632257eea380cabd2e87ced352a'
        inner = dynamic(args,5)
        assert inner[:4] == keccak256(b'finalizeBridgeERC20(address,address,address,address,uint256,bytes)')[:4]
        args = inner[4:]
        assert ['0x'+args[i*32:(i+1)*32][-20:].hex() for i in range(4)] == [remote,local,BASE_ALM,ETH_ALM]
        assert int.from_bytes(args[128:160]) == raw_amount
        assert dynamic(args,5) == b''
        # Recompute the portal withdrawal hash from all six ABI arguments.
        encoded = b''.join(bytes.fromhex(r[k][2:]) for k in ('topic1','topic2','topic3'))
        encoded += body[:64]+(192).to_bytes(32)+len(payload).to_bytes(32)+payload
        encoded += b'\0'*((-len(payload))%32)
        withdrawal_hash = '0x'+keccak256(encoded).hex()
        assert withdrawal_hash == '0x'+body[96:128].hex()
        assert any(x['topic0']==topic('RelayedMessage(bytes32)')
                   and x['address']=='0x866e82a600a1414e583f7f13623f1ac5d58b0afa'
                   and x['topic1']=='0x'+keccak256(payload).hex() and x['transaction_hash']==tx
                   for x in f['ethereum'])
        assert any(x['topic0']==topic('WithdrawalFinalized(bytes32,bool)')
                   and x['address']=='0x49048044d57e1c92a77f79988d21fa8faf74e97e'
                   and x['topic1']==withdrawal_hash and int(x['data'],16)==1
                   and x['transaction_hash']==tx for x in f['ethereum'])
        assert any(x['topic0']==TRANSFER_TOPIC0 and x['address']==local
                   and x['topic1'].endswith(BASE_ALM[2:]) and int(x['topic2'],16)==0
                   and int(x['data'],16)==raw_amount for x in f['base'])
        assert any(x['topic0']==TRANSFER_TOPIC0 and x['address']==remote
                   and x['topic1'].endswith('7f311a4d48377030bd810395f4ccfc03bdbe9ef3')
                   and x['topic2'].endswith(ETH_ALM[2:]) and int(x['data'],16)==raw_amount
                   and x['transaction_hash']==tx for x in f['ethereum'])
