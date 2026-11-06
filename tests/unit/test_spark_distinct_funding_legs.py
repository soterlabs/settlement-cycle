import json
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0


def test_actual_saver_cash_and_sky_draw_have_distinct_authenticated_token_paths():
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/spark_distinct_saver_and_sky_funding.json').read_text())
    b, receipt = fixture['batch'], fixture['receipt']
    assert receipt['status'] == '0x1' and int(receipt['blockNumber'], 16) == b['block'] == 25007787
    assert 'ethereum:' + receipt['transactionHash'] == b['identity']
    logs = receipt['logs']
    alm = '1601843c5e9bc251a3272907010afa41fa18347e'
    usds = '0xdc035d45d973e3ec169d2276ddab16f1e407384f'
    susds = '0xa3931d71877c0e7a3148cb7eb4463524fec27fbd'
    buffer = 'c395d150e71378b47a1b8e9de0c1a83b75a08324'
    vault = '0xe2e7a17dff93280dec073c995595155283e3c372'
    cash = next(m for m in b['movements'] if m['account'] == f'ethereum:0x{alm}:{usds}')
    assert D(cash['value_before']) == D(cash['change']) == 0
    ingress = [r for r in logs if r['address'] == usds and r['topics'][0] == TRANSFER_TOPIC0 and r['topics'][2].endswith(alm)]
    out = [r for r in logs if r['address'] == usds and r['topics'][0] == TRANSFER_TOPIC0 and r['topics'][1].endswith(alm)]
    assert len(ingress) == len(out) == 1
    assert ingress[0]['topics'][1].endswith(buffer) and out[0]['topics'][2].endswith(susds[2:])
    amount = int(ingress[0]['data'], 16)
    assert amount == int(out[0]['data'], 16)
    with localcontext() as ctx:
        ctx.prec = 60
        assert abs(D(amount)/10**18 - D(b['minted'])) < D('1e-18')
    deposit_topic = '0x' + keccak256(b'Deposit(address,address,uint256,uint256)').hex()
    deposit = next(r for r in logs if r['address'] == susds and r['topics'][0] == deposit_topic)
    assert deposit['topics'][1].endswith(alm) and deposit['topics'][2].endswith(alm)
    assert int(deposit['data'][2:66], 16) == amount
    shares = int(deposit['data'][66:], 16)
    assert any(r['address'] == susds and r['topics'][0] == TRANSFER_TOPIC0
               and int(r['topics'][1], 16) == 0 and r['topics'][2].endswith(alm)
               and int(r['data'], 16) == shares for r in logs)
    take_topic = '0x' + keccak256(b'Take(address,uint256)').hex()
    take = next(r for r in logs if r['address'] == vault and r['topics'][0] == take_topic)
    assert take['topics'][1].endswith(alm)
    assert D(int(take['data'], 16))/10**6 == D('180000802.451341')
    assert any(r['address'] == '0xdac17f958d2ee523a2206206994597c13d831ec7'
               and r['topics'][0] == TRANSFER_TOPIC0 and r['topics'][1].endswith(vault[2:])
               and r['topics'][2].endswith(alm) and int(r['data'], 16) == int(take['data'], 16) for r in logs)


def _history():
    from datetime import date

    from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

    b=json.loads((Path(__file__).parents[1]/'fixtures/spark_distinct_saver_and_sky_funding.json').read_text())['batch']
    batch=CapitalBatch(b['identity'],date.fromisoformat(b['day']),b['timestamp'],b['chain'],b['block'],
        tuple(AssetMovement(m['account'],D(m['value_before']),D(m['change']),D(m['external_income']),m['preserve_basis']) for m in b['movements']),
        D(b['minted']),b['log_index'],{k:D(v) for k,v in b['minted_by_ilk'].items()})
    return CapitalHistory((batch,),{str(i):m.account for i,m in enumerate(batch.movements)}, {})


def test_sky_draw_stays_entirely_in_susds_without_certifying_saver_funding(monkeypatch):
    from settle.compute.allocation_capital import replay_history
    from settle.compute.spark_separate_savings_routes import (
        SAVER,
        SUSDS,
        USDT,
        separate_spark_savings_routes,
    )

    h=_history()
    day=h.batches[0].day
    with monkeypatch.context() as patch:
        patch.setattr("settle.compute.spark_separate_savings_routes.separate_spark_savings_routes",lambda h:h)
        before=replay_history(h,day,day)
    after=replay_history(separate_spark_savings_routes(h),day,day)
    assert before.ledger.account(USDT).borrowed>D('90000000')
    assert after.ledger.account(USDT).borrowed==0
    assert abs(after.ledger.account(SUSDS).borrowed-D('180000066.289061952380736771'))<D('1e-18')
    assert after.ledger.drawn_by_ilk==before.ledger.drawn_by_ilk
    assert after.ledger.repaid_by_ilk==before.ledger.repaid_by_ilk
    assert after.unmatched_receipts[SAVER]>D('180000000')
    assert USDT in after.uncertain_accounts
    assert SUSDS not in after.uncertain_accounts
    assert after.ledger.realised_principal_loss==0


def test_split_keeps_all_movements_fee_corrections_and_debt_and_is_idempotent():
    from dataclasses import replace

    from settle.compute.spark_separate_savings_routes import MORPHO, separate_spark_savings_routes

    h=_history()
    b=h.batches[0]
    b=replace(b,movements=tuple(replace(m,external_income=D('1.05274290661856'))
                               if m.account==MORPHO else m for m in b.movements))
    h=replace(h,batches=(b,))
    fixed=separate_spark_savings_routes(h)
    assert separate_spark_savings_routes(fixed)==fixed
    assert sorted((m for b in fixed.batches for m in b.movements),key=lambda m:m.account)==sorted(b.movements,key=lambda m:m.account)
    with localcontext() as ctx:
        ctx.prec=60
        assert sum(x.minted for x in fixed.batches)==b.minted
    assert separate_spark_savings_routes(replace(h,venue_accounts={}))==replace(h,venue_accounts={})


def test_changed_debt_or_usds_route_and_partial_split_fail():
    from dataclasses import replace

    import pytest

    from settle.compute.spark_separate_savings_routes import SUSDS, separate_spark_savings_routes

    h=_history()
    b=h.batches[0]
    with pytest.raises(ValueError,match='metadata or debt'):
        separate_spark_savings_routes(replace(h,batches=(replace(b,minted=b.minted+1),)))
    with pytest.raises(ValueError,match='USDS route'):
        separate_spark_savings_routes(replace(h,batches=(replace(b,movements=tuple(
            replace(m,change=m.change+1) if m.account==SUSDS else m for m in b.movements)),)))
    fixed=separate_spark_savings_routes(h)
    with pytest.raises(ValueError,match='Incomplete or mixed'):
        separate_spark_savings_routes(replace(fixed,batches=fixed.batches[:1]))
