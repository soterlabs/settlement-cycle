import gzip
import importlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_history_cache import load_history

ROOT=Path(__file__).resolve().parents[2]
FIXTURES=ROOT/'tests/fixtures'


@pytest.fixture(scope='module')
def evidence():
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(ROOT/'scripts'))
        draw=importlib.import_module('audit_spark_ordered_draws')
        returns=importlib.import_module('audit_spark_spusds_returns')
        h=load_history(FIXTURES/'spark_susds_origin_history.jsonl.gz','spark-origin-slim')
        source=load_history(FIXTURES/'spark_spusds_origin_history.jsonl.gz','spark-spusds-origin')
        native=load_history(FIXTURES/'spark_native_origin_support.jsonl.gz','spark-native-support')
        batches={b.identity:b for b in h.batches}
        for b in (*source.batches,*native.batches):
            if b.identity in batches:
                assert batches[b.identity]==b
            batches[b.identity]=b
        h=replace(native,batches=tuple(batches.values()))
        receipts=json.load(gzip.open(FIXTURES/'spark_susds_all_month_receipts.json.gz'))
        yield draw,returns,h,source,receipts


def test_early_deposits_and_prior_cash_payment_are_receipt_proven(evidence):
    draw,_returns,h,_source,receipts=evidence
    expected={
        '0x18a868a84346a0c2ca648315aca83a9aa949c205bf7a9b54dc7a0408730b292a':'149999123.096248047653076562',
        '0x8169a8a6f61747f73bf5194d945271592830019e5f1f8c80b321314e9006c873':'170000013.6692794580817482739',
        '0x9924ef53ce3e3a16c6dc01ecbb8329cb449e1291d07b9399591288ea138239aa':'44999957.30583241845446110462',
    }
    index={b.identity:b for b in h.batches}
    for tx,amount in expected.items():
        proof=draw.validate(index['ethereum:'+tx],receipts[tx],allow_mixed_prefix=True,allow_prefix_payments=True)
        assert D(proof['verified_borrowed_contribution'])==D(amount)
    tx=list(expected)[-1]
    with pytest.raises(ValueError,match='contaminates'):
        draw.validate(index['ethereum:'+tx],receipts[tx],allow_mixed_prefix=True)
    bad=deepcopy(receipts[tx])
    payment=next(log for log in bad['logs'] if draw.index(log)==552)
    payment['data']=hex(int(payment['data'],16)+10**18)
    with pytest.raises(ValueError,match='cash'):
        draw.validate(index['ethereum:'+tx],bad,allow_mixed_prefix=True,allow_prefix_payments=True)


def test_spusds_return_carries_only_source_borrowing_and_requires_exact_cash(evidence):
    _draw,returns,h,source,receipts=evidence
    tx='0x49e2233a80df690363e35626f30ac8ac6be8a638ad07dc23640a5aa33716a3e8'
    b=next(b for b in h.batches if b.identity=='ethereum:'+tx)
    assert returns.validate(b,receipts[tx])==D('54600216.25078286042722843')
    proofs,_=returns.contributions(source,{tx:receipts[tx]},date(2026,8,31))
    assert len(proofs)==1
    assert D(proofs[0]['verified_borrowed_contribution']).quantize(D('.01'))==D('49437308.68')
    assert D(proofs[0]['unclassified_cash'])>D('5000000')
    unfunded=replace(source,batches=tuple(replace(b,minted=D(0),minted_by_ilk={}) for b in source.batches))
    proofs,_=returns.contributions(unfunded,{tx:receipts[tx]},date(2026,8,31))
    assert D(proofs[0]['verified_borrowed_contribution'])==0
    bad=deepcopy(receipts[tx])
    next(log for log in bad['logs'] if returns.index(log)==562)['topics'][1]='0x'+'1'*64
    with pytest.raises(ValueError,match='does not match'):
        returns.validate(b,bad)


def test_full_month_receipts_match_full_history_artifact(evidence):
    _draw,_returns,h,_source,receipts=evidence
    from audit_spark_savings_origin import attribute
    result=attribute(h,receipts,date(2026,1,1),date(2026,8,31),allow_mixed_prefix=True,
                     allow_prefix_payments=True,include_native_returns=True,include_spusds_returns=True)
    full=json.load(gzip.open(ROOT/'reconciliation/spark_susds_all_month_origins_2026_08.json.gz'))
    for key in ('closing_capital','closing_borrowed','remaining_origin_to_classify',
                'verified_routes','rejected_routes','spusds_return_proofs','surviving_unclassified_sources'):
        assert result[key]==full[key]
    assert D(result['closing_capital']).quantize(D('.01'))==D('703133724.15')
    assert D(result['closing_borrowed'])>D('648032123.81')
    assert result['spusds_return_proofs']
