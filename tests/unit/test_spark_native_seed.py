import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_native_seed import COST, ILK, SEEDS, SOURCE, link_spark_native_seed
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

DAY = date(2025,6,2)


def history():
    source=CapitalBatch(SOURCE,DAY,1748875655,'ethereum',22617678,(),4*COST,minted_by_ilk={ILK:4*COST})
    receipts=tuple(CapitalBatch(chain+':'+tx,DAY,stamp,chain,block,
        (AssetMovement(f'{chain}:{holder}:{token}',D(0),value),))
        for chain,holder,token,tx,block,stamp,value in SEEDS)
    return CapitalHistory((source,*receipts),{}, {})


def test_exact_draw_funds_four_destinations_without_borrowing_the_savings_growth():
    h=history()
    linked=link_spark_native_seed(h)
    assert link_spark_native_seed(linked)==linked
    r=replay_history(linked,DAY,DAY)
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert r.ledger.drawn_by_ilk=={ILK:4*COST}
    assert sum(r.daily[DAY].values())==4*COST
    for chain,holder,token,*_ in SEEDS:
        assert r.ledger.account(f'{chain}:{holder}:{token}').borrowed==COST
    assert len(linked.analytics_only_venues)==4
    assert not linked.idle_accounts


def test_missing_receipts_retain_custody_and_existing_source_custody_cannot_be_double_counted():
    h=history()
    cutoff=replace(h,batches=h.batches[:1])
    r=replay_history(link_spark_native_seed(cutoff),DAY,DAY)
    assert not r.unmatched_outflows
    assert sum(r.daily[DAY].values())==4*COST
    bad=replace(h.batches[0],movements=(AssetMovement('already observed',D(0),4*COST),))
    with pytest.raises(ValueError,match='existing custody'):
        link_spark_native_seed(replace(h,batches=(bad,*h.batches[1:])))
    bad=replace(h.batches[0],minted_by_ilk={'different ilk':4*COST})
    with pytest.raises(ValueError,match='draw'):
        link_spark_native_seed(replace(h,batches=(bad,*h.batches[1:])))
    lone=replace(h,batches=h.batches[1:])
    assert link_spark_native_seed(lone)==lone


def topic(signature):
    return '0x'+keccak256(signature.encode()).hex()


def test_subproxy_cash_and_canonical_relay_hashes_prove_all_four_deliveries():
    f=json.loads((Path(__file__).parents[1]/'fixtures/spark_june_2025_native_seed.json').read_text())
    source=f['ethereum']
    subproxy='3300f198988e4c9c63f75df86de36421f06af8c4'
    usds='0xdc035d45d973e3ec169d2276ddab16f1e407384f'
    susds='0xa3931d71877c0e7a3148cb7eb4463524fec27fbd'
    payments=[r for r in source if r['topic0']==TRANSFER_TOPIC0]
    assert any(r['address']==usds and r['topic1'].endswith('c395d150e71378b47a1b8e9de0c1a83b75a08324')
               and r['topic2'].endswith(subproxy) and int(r['data'],16)==400000000*10**18 for r in payments)
    assert any(r['address']==usds and r['topic1'].endswith(subproxy)
               and r['topic2'].endswith(susds[2:]) and int(r['data'],16)==200000000*10**18 for r in payments)
    decoded=[]
    for r in source:
        if r['topic0']!=topic('SentMessage(address,address,bytes,uint256,uint256)'):
            continue
        body=bytes.fromhex(r['data'][2:])
        offset=int.from_bytes(body[32:64])
        size=int.from_bytes(body[offset:offset+32])
        payload=body[offset+32:offset+32+size]
        if payload[:4]!=keccak256(b'finalizeBridgeERC20(address,address,address,address,uint256,bytes)')[:4]:
            continue
        args=payload[4:]
        token,origin,owner,recipient=['0x'+args[i*32:(i+1)*32][-20:].hex() for i in range(4)]
        amount=int.from_bytes(args[128:160])
        assert owner=='0x'+subproxy
        ext=next(x for x in source if x['log_index']==r['log_index']+1)
        assert ext['address']==r['address'] and ext['topic0']==topic('SentMessageExtension1(address,uint256)')
        assert int(ext['data'],16)==0
        encoded=keccak256(b'relayMessage(uint256,address,address,uint256,uint256,bytes)')[:4]
        encoded+=body[64:96]+body[:32]+bytes.fromhex(r['topic1'][2:])+bytes(32)+body[96:128]+(192).to_bytes(32)
        encoded+=len(payload).to_bytes(32)+payload+bytes((-len(payload))%32)
        hash_='0x'+keccak256(encoded).hex()
        message=next(m for m in f['messages'] if m['hash']==hash_)
        chain=message['chain']
        escrow={'optimism':'467194771dae2967aef3ecbedd3bf9a310c76c65',
                'unichain':'1196f688c585d3e5c895ef8954ffb0dcdafc566a'}[chain]
        assert any(p['address']==origin and p['topic1'].endswith(subproxy)
                   and p['topic2'].endswith(escrow) and int(p['data'],16)==amount for p in payments)
        rows=next(g['rows'] for g in f['groups'] if g['chain']==chain)
        receipt=next(x for x in rows if x['topic0']==topic('RelayedMessage(bytes32)') and x['topic1']==hash_)
        assert receipt['address']=='0x4200000000000000000000000000000000000007'
        mint=next(x for x in rows if x['transaction_hash']==receipt['transaction_hash'] and x['topic0']==TRANSFER_TOPIC0)
        assert mint['address']==token and int(mint['topic1'],16)==0
        assert mint['topic2'].endswith(recipient[2:]) and int(mint['data'],16)==amount
        decoded.append((chain,recipient,token,receipt['transaction_hash']))
    assert set(decoded)=={leg[:4] for leg in SEEDS}
