import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_op_uni_withdrawals import ETH_ALM, ROUTES, link_spark_op_uni_withdrawals
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

PROOF = json.loads((Path(__file__).parents[1] / 'fixtures/spark_op_uni_withdrawals.json').read_text())
L1 = {
    'optimism': ('0x25ace71c97b33cc4729cf772ae268934f7ab5fa1',
                 '0xbeb5fc579115071764c7423a4f12edde41f106ed',
                 '0x467194771dae2967aef3ecbedd3bf9a310c76c65'),
    'unichain': ('0x9a3d64e386c18cb1d6d5179a9596a4b5736e98a6',
                 '0x0bd48f6b86a26d3a217d0fa6ffe2b491b956a7a2',
                 '0x1196f688c585d3e5c895ef8954ffb0dcdafc566a'),
}


def topic(s):
    return '0x' + keccak256(s.encode()).hex()


def dynamic(raw, word):
    off = int.from_bytes(raw[word*32:(word+1)*32])
    size = int.from_bytes(raw[off:off+32])
    value = raw[off+32:off+32+size]
    assert len(value) == size
    return value


def test_all_four_canonical_withdrawals_match_ethereum_finalizations_and_payments():
    count = 0
    for chain, holder, source_tx, block, stamp, legs in ROUTES:
        rows = next(s['rows'] for s in PROOF['sources'] if s['chain'] == chain)
        assert all(r['transaction_hash'] == source_tx and r['block_number'] == block
                   and r['block_time'] == stamp for r in rows)
        messages = [r for r in rows if r['address'] == '0x4200000000000000000000000000000000000016'
            and r['topic0'] == topic('MessagePassed(uint256,address,address,uint256,uint256,bytes,bytes32)')]
        assert len(messages) == len(legs) == 2
        messenger, portal, escrow = L1[chain]
        for r, (local, remote, amount, tx, _, _, db, ds) in zip(messages, legs, strict=True):
            raw = bytes.fromhex(r['data'][2:])
            assert r['topic2'].endswith('4200000000000000000000000000000000000007')
            assert r['topic3'].endswith(messenger[2:])
            payload = dynamic(raw, 2)
            assert payload[:4] == keccak256(b'relayMessage(uint256,address,address,uint256,uint256,bytes)')[:4]
            args = dynamic(payload[4:], 5)
            assert args[:4] == keccak256(b'finalizeBridgeERC20(address,address,address,address,uint256,bytes)')[:4]
            args = args[4:]
            assert ['0x' + args[n*32:(n+1)*32][-20:].hex() for n in range(4)] == [remote, local, holder, ETH_ALM]
            assert int.from_bytes(args[128:160]) == amount and dynamic(args, 5) == b''
            encoded = b''.join(bytes.fromhex(r[k][2:]) for k in ('topic1', 'topic2', 'topic3'))
            encoded += raw[:64] + (192).to_bytes(32) + len(payload).to_bytes(32) + payload + bytes((-len(payload)) % 32)
            withdrawal_hash = '0x' + keccak256(encoded).hex()
            assert withdrawal_hash == '0x' + raw[96:128].hex()
            dest = [x for x in PROOF['ethereum'] if x['transaction_hash'] == tx]
            assert all(x['block_number'] == db and x['block_time'] == ds for x in dest)
            assert any(x['topic0'] == topic('RelayedMessage(bytes32)') and x['address'] == messenger
                and x['topic1'] == '0x' + keccak256(payload).hex() for x in dest)
            assert any(x['topic0'] == topic('WithdrawalFinalized(bytes32,bool)') and x['address'] == portal
                and x['topic1'] == withdrawal_hash and int(x['data'], 16) == 1 for x in dest)
            assert any(x['topic0'] == TRANSFER_TOPIC0 and x['address'] == local
                and x['topic1'].endswith(holder[2:]) and int(x['topic2'], 16) == 0
                and int(x['data'], 16) == amount for x in rows)
            assert any(x['topic0'] == TRANSFER_TOPIC0 and x['address'] == remote
                and x['topic1'].endswith(escrow[2:]) and x['topic2'].endswith(ETH_ALM[2:])
                and int(x['data'], 16) == amount for x in dest)
            count += 1
    assert count == 4


def history(route):
    chain, holder, tx, block, stamp, legs = route
    day = datetime.fromtimestamp(stamp, UTC).date()
    funding, outgoing, arrivals = [], [], []
    for local, remote, _, received, source, arrival, db, ds in legs:
        account = f'{chain}:{holder}:{local}'
        funding.append(AssetMovement(account, D(0), source, external_income=source - D('10000000')))
        outgoing.append(AssetMovement(account, source, -source))
        arrivals.append(CapitalBatch('ethereum:' + received, datetime.fromtimestamp(ds, UTC).date(), ds,
            'ethereum', db, (AssetMovement(f'ethereum:{ETH_ALM}:{remote}', D(0), arrival),)))
    fund = CapitalBatch('funding', day, stamp - 1, chain, block - 1, tuple(funding), D('20000000'),
        minted_by_ilk={'SPARK': D('20000000')})
    source = CapitalBatch(chain + ':' + tx, day, stamp, chain, block, tuple(outgoing))
    return CapitalHistory((fund, source, *arrivals), {}, {})


@pytest.mark.parametrize('route', ROUTES)
def test_funding_survives_the_seven_day_delay_without_idle_exemption(route):
    h = history(route)
    linked = link_spark_op_uni_withdrawals(h)
    assert linked == link_spark_op_uni_withdrawals(linked)
    r = replay_history(linked, date(2026, 7, 6), date(2026, 7, 13))
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert r.ledger.drawn == D('20000000')
    for _, remote, *_rest in route[-1]:
        assert abs(r.ledger.account(f'ethereum:{ETH_ALM}:{remote}').borrowed - D('10000000')) < D('1e-18')
    claims = set(linked.venue_accounts.values())
    assert abs(sum(r.daily[date(2026, 7, 7)][c] for c in claims) - D('20000000')) < D('1e-18')
    assert all(r.daily[date(2026, 7, 13)][c] == 0 for c in claims)
    assert not linked.idle_accounts
    cutoff = replace(h, batches=h.batches[:2])
    r = replay_history(link_spark_op_uni_withdrawals(cutoff), date(2026, 7, 6), date(2026, 7, 13))
    assert not r.unmatched_outflows


def test_wrong_or_partially_transformed_routes_fail_and_unrelated_history_is_unchanged():
    h = history(ROUTES[0])
    source = replace(h.batches[1], block=1)
    with pytest.raises(ValueError, match='source differs'):
        link_spark_op_uni_withdrawals(replace(h, batches=(h.batches[0], source, *h.batches[2:])))
    receipt = replace(h.batches[2], movements=(replace(h.batches[2].movements[0], change=D(1)),))
    with pytest.raises(ValueError, match='receipt differs'):
        link_spark_op_uni_withdrawals(replace(h, batches=(*h.batches[:2], receipt, h.batches[3])))
    linked = link_spark_op_uni_withdrawals(h)
    with pytest.raises(ValueError, match='append raw'):
        link_spark_op_uni_withdrawals(replace(linked, batches=(*linked.batches, h.batches[2])))
    other = CapitalHistory((), {}, {}, analytics_only_venues=('E2', 'E1'))
    assert link_spark_op_uni_withdrawals(other) is other
