import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_ustb_capital import ACCOUNT, CASH, MARKS, RETURNS, link_spark_ustb
from settle.domain.config import load_prime_by_id
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize import allocation_superstate as pricing
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

F = json.loads((Path(__file__).parents[1] / 'fixtures/spark_ustb_capital.json').read_text())


def batch(raw):
    return CapitalBatch(raw['identity'], date.fromisoformat(raw['day']), raw['timestamp'], raw['chain'],
        raw['block'], tuple(AssetMovement(m['account'], D(m['value_before']), D(m['change']),
        D(m['external_income']), m['preserve_basis']) for m in raw['movements']), D(raw['minted']),
        raw['log_index'], {k: D(v) for k,v in raw['minted_by_ilk'].items()})


def history():
    return CapitalHistory(tuple(batch(x['batch']) for x in F['transactions']) +
                          tuple(batch(x) for x in F['cash_batches']), {'S21': ACCOUNT}, {})


def test_six_atomic_subscriptions_pay_300m_and_match_execution_oracle_to_one_raw_share():
    subscribe = '0x' + keccak256(b'Subscribe(address,address,uint256,uint256,uint256)').hex()
    paid_total = 0
    for witness, mark, quote in zip(F['transactions'], MARKS, F['token_oracle']['reads'], strict=True):
        tx, block, stamp, before, change, nav = mark
        b, r = witness['batch'], witness['receipt']
        assert r['transactionHash'] == tx and r['status'] == '0x1'
        assert int(r['blockNumber'],16) == block == b['block'] == quote['block']
        assert stamp == b['timestamp'] == quote['timestamp']
        assert quote['oracle'] == '0xe4fa682f94610ccd170680cc3b045d77d9e528a8'
        assert D(int(quote['latestRoundData()'][66:130],16))/10**6 == nav
        m = next(m for m in b['movements'] if m['account'] == ACCOUNT)
        assert D(m['value_before']) == before and D(m['change']) == change
        if change <= 0:
            continue
        logs = [x for x in r['logs'] if x['address'] == pricing.USTB and x['topics'][0] == subscribe]
        assert len(logs) == 1
        event = logs[0]
        assert event['topics'][1].endswith(ACCOUNT.split(':')[1][2:])
        words = [int(event['data'][i:i+64],16) for i in range(2,len(event['data']),64)]
        assert words[0] == int(CASH.split(':')[2],16)
        paid, after_fee, shares = words[1:]
        assert paid == after_fee  # no subscription fee in these six executions
        assert D(shares)/10**6 == change
        assert 0 <= D(paid)/10**6-change*nav < nav/D(10**6)
        assert any(x['address'] == CASH.split(':')[2] and x['topics'][0] == TRANSFER_TOPIC0
                   and x['topics'][1].endswith(ACCOUNT.split(':')[1][2:])
                   and x['topics'][2].endswith('774ae279c21b6a17a6e2bd5ab5398ff98f398807')
                   and int(x['data'],16) == paid for x in r['logs'])
        assert any(x['address'] == pricing.USTB and x['topics'][0] == TRANSFER_TOPIC0
                   and int(x['topics'][1],16) == 0 and x['topics'][2].endswith(ACCOUNT.split(':')[1][2:])
                   and int(x['data'],16) == shares for x in r['logs'])
        paid_total += paid
    assert paid_total == 300_000_000 * 10**6


def test_offchain_redemptions_and_same_day_cash_match_execution_nav_to_the_cent():
    signature = '0x' + keccak256(b'OffchainRedeem(address,address,uint256)').hex()
    for burn, payment, cash, block, stamp in RETURNS:
        witness = next(x for x in F['transactions'] if x['receipt']['transactionHash'] == burn)
        event = next(x for x in witness['receipt']['logs'] if x['topics'][0] == signature)
        assert event['address'] == pricing.USTB
        assert all(x.endswith(ACCOUNT.split(':')[1][2:]) for x in event['topics'][1:])
        mark = next(m for m in MARKS if m[0] == burn)
        assert D(int(event['data'],16))/10**6 == -mark[4]
        assert (-mark[4]*mark[5]).quantize(D('.01')) == cash
        receipt = next(x for x in F['cash_payments'] if x['transaction_hash'] == payment)
        assert receipt['address'] == CASH.split(':')[2] and receipt['topic0'] == TRANSFER_TOPIC0
        assert receipt['topic1'].endswith('55fe002aeff02f77364de339a1292923a15844b8')
        assert receipt['topic2'].endswith(ACCOUNT.split(':')[1][2:])
        assert D(int(receipt['data'],16))/10**6 == cash
        assert receipt['block_number'] == block and receipt['block_time'] == stamp
        assert datetime.fromtimestamp(stamp, UTC).date() == date.fromisoformat(witness['batch']['day'])
    # The other two payments by this shared payer occur in December. They
    # remain outside these exact reviewed associations (USCC candidates).
    assert len(F['common_payer_inventory']['rows']) == 4


def test_replay_preserves_paid_principal_and_does_not_borrow_redemption_gains():
    h = history()
    # Isolated-cycle test: keep the first cash return until the second arrives;
    # the full production replay handles intervening ALM activity separately.
    last = h.batches[-1]
    h = replace(h, batches=(*h.batches[:-1], replace(last, movements=(
        replace(last.movements[0], value_before=RETURNS[0][2]),))))
    fixed = link_spark_ustb(h)
    assert link_spark_ustb(fixed) == fixed
    assert sum(b.minted for b in fixed.batches) == sum(b.minted for b in h.batches)
    r = replay_history(fixed, date(2025,4,7), date(2025,7,17))
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert abs(r.ledger.account(CASH).borrowed-D(300_000_000)) < D('.0001')
    assert r.ledger.account(CASH).value == D('303111546.84')
    assert r.ledger.account(ACCOUNT).borrowed == 0
    assert all(r.ledger.account(a).borrowed == 0 for a in fixed.custody_accounts['S21'])
    assert r.ledger.realised_principal_loss == 0


def test_source_cutoff_retains_claim_and_receipt_alone_creates_no_borrowing():
    h = history()
    cutoff = replace(h,batches=tuple(b for b in h.batches if b.timestamp <= MARKS[-1][2]))
    fixed = link_spark_ustb(cutoff)
    r = replay_history(fixed,date(2025,4,7),date(2025,7,17))
    assert r.ledger.account(fixed.custody_accounts['S21'][-1]).borrowed > D('299999800')
    assert not r.unmatched_outflows
    receipt_only = replace(h,batches=(h.batches[-1],))
    assert link_spark_ustb(receipt_only) == receipt_only
    assert link_spark_ustb(replace(h,venue_accounts={})) == replace(h,venue_accounts={})


def test_changed_cash_or_nav_shape_fails_closed():
    h = history()
    first = h.batches[0]
    bad = replace(first,movements=tuple(replace(m,change=m.change+1) if m.account==ACCOUNT else m for m in first.movements))
    with pytest.raises(ValueError,match='NAV marks'):
        link_spark_ustb(replace(h,batches=(bad,*h.batches[1:])))
    last = h.batches[-1]
    bad = replace(last,movements=(replace(last.movements[0],change=last.movements[0].change+1),))
    with pytest.raises(ValueError,match='receipt amount'):
        link_spark_ustb(replace(h,batches=(*h.batches[:-1],bad)))


def test_runtime_pricer_uses_the_tokens_oracle_at_each_execution_block(monkeypatch):
    token = next(v.token for v in load_prime_by_id('spark').venues if v.id == 'S21')
    for quote, mark in zip(F['token_oracle']['reads'], MARKS, strict=True):
        answers = {('0x'+keccak256(sig.encode())[:4].hex()): value for sig,value in quote.items() if sig.endswith('()')}
        def call(chain, address, data, block, *, quote=quote, answers=answers):
            assert block == quote['block']
            expected = pricing.USTB if data in ('0x'+keccak256(b'superstateOracle()')[:4].hex(),
                '0x'+keccak256(b'maximumOracleDelay()')[:4].hex()) else quote['oracle']
            assert address.hex == expected
            return answers[data]
        monkeypatch.setattr(pricing.rpc,'eth_call',call)
        monkeypatch.setattr(pricing.hypersync,'block_timestamp',lambda *a, q=quote:q['timestamp'])
        assert pricing.ustb_capital_price(token,quote['block']) == mark[5]


@pytest.mark.parametrize('fault', ['oracle_zero', 'decimals', 'negative', 'stale', 'future', 'shape'])
def test_invalid_or_unavailable_ustb_nav_is_not_replaced_with_par(monkeypatch, fault):
    token = next(v.token for v in load_prime_by_id('spark').venues if v.id == 'S21')
    quote = dict(F['token_oracle']['reads'][0])
    if fault == 'oracle_zero':
        quote['superstateOracle()'] = '0x' + '0'*64
    elif fault == 'decimals':
        quote['decimals()'] = '0x' + f'{18:064x}'
    elif fault == 'shape':
        quote['latestRoundData()'] = '0x'
    else:
        words = [int(quote['latestRoundData()'][i:i+64],16) for i in range(2,322,64)]
        if fault == 'negative':
            words[1] = 2**256 - 1
        elif fault == 'stale':
            words[2] = words[3] = quote['timestamp'] - 3601
        else:
            words[2] = words[3] = quote['timestamp'] + 1
        quote['latestRoundData()'] = '0x' + ''.join(f'{w:064x}' for w in words)
    answers = {'0x'+keccak256(sig.encode())[:4].hex(): value for sig,value in quote.items() if sig.endswith('()')}
    monkeypatch.setattr(pricing.rpc,'eth_call',lambda chain,address,data,block:answers[data])
    monkeypatch.setattr(pricing.hypersync,'block_timestamp',lambda *a:quote['timestamp'])
    with pytest.raises(ValueError, match='USTB capital'):
        pricing.ustb_capital_price(token,quote['block'])


def test_capital_pricing_dispatch_overrides_only_ustb_legacy_par_mark(monkeypatch):
    from settle.normalize import allocation_capital as source
    venue = next(v for v in load_prime_by_id('spark').venues if v.id == 'S21')
    monkeypatch.setattr(pricing,'ustb_capital_price',lambda token,block:D('10.631398'))
    monkeypatch.setattr(source,'get_unit_price',lambda *a,**k:D(1))
    assert source._capital_unit_price(venue,MARKS[0][1]) == D('10.631398')
    assert source.get_unit_price(venue,MARKS[0][1]) == D(1)
