import importlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_uscc_capital import (
    ACCOUNT,
    CASH,
    MARKS,
    REDEMPTIONS,
    SUBSCRIPTIONS,
    link_spark_uscc,
)
from settle.domain.config import load_prime_by_id
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize import allocation_superstate as pricing
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROOT = Path(__file__).resolve().parents[2]
F = json.loads((ROOT / 'tests/fixtures/spark_uscc_history.json').read_text())
RAW = json.loads((ROOT / 'tests/fixtures/spark_uscc_capital_batches.json').read_text())
NAV = json.loads((ROOT / 'tests/fixtures/spark_uscc_nav.json').read_text())
SETTLEMENT = json.loads((ROOT / 'tests/fixtures/spark_uscc_settlement_nav.json').read_text())


def history():
    batches = tuple(CapitalBatch(b['identity'],date.fromisoformat(b['day']),b['timestamp'],b['chain'],b['block'],
        tuple(AssetMovement(m['account'],D(m['value_before']),D(m['change']),D(m['external_income']),
                            m['preserve_basis']) for m in b['movements']),D(b['minted']),b['log_index'],
        {k:D(v) for k,v in b['minted_by_ilk'].items()}) for b in RAW)
    return CapitalHistory(batches,{'S22':ACCOUNT},{})


def test_closed_subscription_groups_have_exact_paid_cash_and_issuer_mints():
    paid_total = D(0)
    for payments,issue in SUBSCRIPTIONS:
        prior = 0
        for tx,block,stamp,paid in payments:
            rows = [r for r in F['issuer_transactions'] if r['transaction_hash']==tx
                    and r['address']==CASH.split(':')[-1] and r['topic0']==TRANSFER_TOPIC0
                    and r['topic1'].endswith(ACCOUNT.split(':')[1][2:])
                    and r['topic2'].endswith('db48ac0802f9a79145821a5430349caff6d676f7')]
            assert len(rows)==1 and rows[0]['block_number']==block and rows[0]['block_time']==stamp
            assert D(int(rows[0]['data'],16))/10**6==paid
            paid_total+=paid
            assert block>prior
            prior=block
        mint = next(r for r in F['issuer_transactions'] if r['transaction_hash']==issue
                    and r['address']==pricing.USCC and r['topic0']==TRANSFER_TOPIC0
                    and int(r['topic1'],16)==0 and r['topic2'].endswith(ACCOUNT.split(':')[1][2:]))
        mark=next(r for r in MARKS if r[0]==issue)
        assert D(int(mint['data'],16))/10**6==mark[4]
        assert mint['block_number']==mark[1]>prior
    assert paid_total==D(150010000)


def test_delayed_cash_matches_settlement_nav_not_request_day_nav(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    inventory=importlib.import_module('audit_spark_uscc_history')
    prices={q['block']:D(q['price']) for q in inventory.nav_inventory(SETTLEMENT)['quotes']}
    inventory.nav_inventory(NAV)
    signature='0x'+keccak256(b'OffchainRedeem(address,address,uint256)').hex()
    for burns,payment,cash,block,stamp,nav in REDEMPTIONS:
        shares=D(0)
        request_value=D(0)
        for burn in burns:
            mark=next(r for r in MARKS if r[0]==burn)
            event=next(r for r in F['issuer_transactions'] if r['transaction_hash']==burn
                       and r['address']==pricing.USCC and r['topic0']==signature)
            assert event['topic1'].endswith(ACCOUNT.split(':')[1][2:])
            assert event['topic2'].endswith(ACCOUNT.split(':')[1][2:])
            assert D(int(event['data'],16))/10**6==-mark[4]
            assert mark[1]<block and stamp>mark[2]
            shares-=mark[4]
            request_value-=mark[4]*mark[5]
        receipt=next(r for r in F['unassigned_cash_candidates'] if r['transaction_hash']==payment)
        assert receipt['block_number']==block and receipt['block_time']==stamp
        assert receipt['address']==CASH.split(':')[-1] and receipt['topic0']==TRANSFER_TOPIC0
        assert receipt['topic1'].endswith('55fe002aeff02f77364de339a1292923a15844b8')
        assert receipt['topic2'].endswith(ACCOUNT.split(':')[1][2:])
        assert D(int(receipt['data'],16))/10**6==cash
        assert prices[block]==nav
        assert abs(shares*nav-cash)<D('.01')
        assert cash-request_value>D(10000)  # request NAV would misstate this payment


def test_complete_cycle_carries_paid_basis_and_excludes_all_nav_gains():
    h=history()
    # Keep the first payment in cash in this isolated-cycle test; full replay
    # separately accounts for the intervening ALM investments/repayments.
    last=h.batches[-1]
    h=replace(h,batches=(*h.batches[:-1],replace(last,movements=(
        replace(last.movements[0],value_before=REDEMPTIONS[0][2]),))))
    fixed=link_spark_uscc(h)
    assert link_spark_uscc(fixed)==fixed
    assert sum(b.minted for b in h.batches)==sum(b.minted for b in fixed.batches)
    r=replay_history(fixed,date(2025,10,20),date(2025,12,4))
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert abs(r.ledger.account(CASH).borrowed-D(150010000))<D('1e-15')
    assert r.ledger.account(CASH).value==D('151013951.26')
    assert r.ledger.account(ACCOUNT).borrowed==0
    assert all(r.ledger.account(c).borrowed==0 for c in fixed.custody_accounts['S22'])
    assert r.ledger.realised_principal_loss==0


def test_partial_funding_and_redemption_cutoffs_retain_real_claims_only():
    h=history()
    first=replace(h,batches=h.batches[:1])
    fixed=link_spark_uscc(first)
    r=replay_history(fixed,date(2025,10,20),date(2025,10,20))
    assert r.ledger.account(fixed.custody_accounts['S22'][0]).borrowed==D(10000)
    cutoff=replace(h,batches=tuple(b for b in h.batches if b.day<=date(2025,12,1)))
    r=replay_history(link_spark_uscc(cutoff),date(2025,10,20),date(2025,12,1))
    assert not r.unmatched_outflows
    assert abs(sum(r.daily[date(2025,12,1)].values())-D(150010000))<D('1e-15')
    receipt_only=replace(h,batches=(h.batches[-1],))
    assert link_spark_uscc(receipt_only)==receipt_only
    assert link_spark_uscc(replace(h,venue_accounts={}))==replace(h,venue_accounts={})


def test_already_nav_priced_input_has_the_same_result_as_legacy_par_snapshot():
    h=history()
    marks={'ethereum:'+r[0]:r for r in MARKS}
    current=replace(h,batches=tuple(replace(b,movements=tuple(replace(m,
        value_before=m.value_before*marks[b.identity][5],change=m.change*marks[b.identity][5])
        for m in b.movements)) if b.identity in marks else b for b in h.batches))
    assert link_spark_uscc(current)==link_spark_uscc(h)


def test_missing_group_leg_and_changed_payout_are_rejected():
    h=history()
    with pytest.raises(ValueError,match='Incomplete USCC subscription'):
        link_spark_uscc(replace(h,batches=h.batches[1:]))
    missing='ethereum:'+REDEMPTIONS[0][0][0]
    with pytest.raises(ValueError,match='Incomplete USCC redemption'):
        link_spark_uscc(replace(h,batches=tuple(b for b in h.batches if b.identity!=missing)))
    last=h.batches[-1]
    bad=replace(last,movements=(replace(last.movements[0],change=last.movements[0].change+1),))
    with pytest.raises(ValueError,match='receipt amount'):
        link_spark_uscc(replace(h,batches=(*h.batches[:-1],bad)))


def test_capital_pricer_matches_all_request_and_settlement_quotes(monkeypatch):
    token=next(v.token for v in load_prime_by_id('spark').venues if v.id=='S22')
    for q in [*NAV['reads'],*SETTLEMENT['reads']]:
        answers={'0x'+keccak256(sig.encode())[:4].hex():v for sig,v in q.items() if sig.endswith('()')}
        def call(chain,address,data,block,*,answers=answers,q=q):
            assert address.hex==pricing.USCC_NAV and block==q['block']
            return answers[data]
        monkeypatch.setattr(pricing.rpc,'eth_call',call)
        monkeypatch.setattr(pricing.hypersync,'block_timestamp',lambda *a,q=q:q['timestamp'])
        assert pricing.uscc_capital_price(token,q['block'])==D(int(q['latestRoundData()'][66:130],16))/10**6


def test_stale_uscc_nav_never_falls_back_to_par(monkeypatch):
    token=next(v.token for v in load_prime_by_id('spark').venues if v.id=='S22')
    q=NAV['reads'][0]
    answers={'0x'+keccak256(sig.encode())[:4].hex():v for sig,v in q.items() if sig.endswith('()')}
    monkeypatch.setattr(pricing.rpc,'eth_call',lambda chain,address,data,block:answers[data])
    updated=int(q['latestRoundData()'][194:258],16)
    monkeypatch.setattr(pricing.hypersync,'block_timestamp',lambda *a:updated+95401)
    with pytest.raises(ValueError,match='USCC capital NAV'):
        pricing.uscc_capital_price(token,q['block'])
