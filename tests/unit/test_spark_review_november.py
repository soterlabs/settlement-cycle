from copy import deepcopy
from decimal import Decimal as D

from settle.revenue.financial import metric
from settle.revenue.financial_spark_review import prepare, presentation
from settle.revenue.cash_flow.classify import collect_ledger
from settle.revenue.cash_flow.config import load, layout
from tests.unit.test_reporting_accounting_rules import transfer, ALM, USDC, USDT


def statement():
    values = {'gains.eth_weth': '10', 'gains.sparklend_nonstable': '100',
              'revaluation.sparklend_assets': '105', 'revaluation.alm_assets': '100', 'revaluation.ops_reserves': '5', 'revaluation.savings_liabilities': '-90',
              'funding.savings.revaluation': '90', 'funding.savings.interest': '5',
              'funding.savings': '95', 'rewards.unattributed_sparklend_receipts': '0',
              'expenses.compensation.spk': '1627925'}
    lines = [dict(id=k, label=k, aggregation='leaf', values={'2026-01': metric(v)}) for k,v in values.items()]
    next(r for r in lines if r['id']=='funding.savings')['components'] = [dict(id='funding.savings.interest', sign=1),dict(id='funding.savings.revaluation', sign=1)]
    return dict(expense_input_revision='a'*64, lines=lines, periods=[dict(key='2026-01',state='complete')], pending_expense_inputs=[dict(id='expenses.compensation.spk')])


def test_paired_principal_fx_is_removed_without_losing_own_gains_or_changing_net_revenue():
    s=statement();before=deepcopy(s);prepare(s,[None]);rows={r['id']:r for r in s['lines']}
    amount=lambda id:D(rows[id]['values']['2026-01']['value'])
    assert amount('gains.eth_weth') == 20  # 10 free ETH + 10 residual own reserve FX.
    assert amount('funding.savings') == 5  # Depositor interest, not principal translation.
    assert amount('gains.other_reserves') == 5
    assert amount('gains.eth_weth')+amount('gains.other_reserves')-amount('funding.savings') == D('10')+D('105')-D('95')
    assert rows['gains.sparklend_nonstable']['include_in_income'] is False
    assert s['pending_expense_inputs'] == []
    # Both indirect cash reversals shrink by 90; actual cash cannot change.
    assert -D('115')+D('95') == -amount('gains.eth_weth')-amount('gains.other_reserves')+amount('funding.savings')
    assert before['lines'][0]['values']['2026-01']['value']=='10'


def test_hidden_statement_groups_remove_descendants_but_keep_auditable_amounts():
    s=statement();prepare(s,[None]);s['statement_rows']=[
      dict(id='gains',parent=None,metric_id='statement.gains',depth=0),
      dict(id='old',parent='gains',metric_id='gains.sparklend_nonstable'),
      dict(id='old-child',parent='old',metric_id='revaluation.sparklend_assets'),
      dict(id='statement.family.other_reviewed_vaults',parent=None,metric_id='statement.family.other_reviewed_vaults'),
      dict(id='dust',parent='statement.family.other_reviewed_vaults',metric_id='investment.fluid')]
    presentation(s)
    assert [r['id'] for r in s['statement_rows']]==['gains','gains.other_reserves']
    assert any(r['id']=='gains.sparklend_nonstable' for r in s['lines'])


def test_contractor_safe_inflows_are_internal_and_outflows_are_expenses():
    config=load();safe='0x8a3c66b30e2bbe5c2b1cff1054d869171fb87766';payee='0x'+'9'*40
    rows=[transfer(USDC,ALM,safe,2700000*10**6),transfer(USDC,safe,payee,1000*10**6,tx='0x'+'a'*64)]
    got=collect_ledger({'chains':{'ethereum':{'events':rows}}},config=config)['entries']
    assert not any(r['line_id']=='unclassified' for r in got)
    assert sum(D(r['amount_usd']) for r in got if r['line_id']=='expenses-paid') == -1000
    assert not any(r['line_id']=='expenses-paid' and r['to']==safe for r in got)


def test_sde_swap_and_principal_are_split_with_investing_cash_unchanged():
    config=load();pool='0x000000000004444c5dc75cb358380d2e3de08a90'
    event=transfer(USDT,ALM,pool,100*10**6,day='2026-06-01',tx='0x'+'b'*64)
    proof=dict(status='available',source_ref='a'*64,entries=[dict(chain='ethereum',transaction=event['tx_hash'],token=USDT,counterparty=pool,venue='S62',principal_raw=str(40*10**6))])
    original=collect_ledger({'chains':{'ethereum':{'events':[event]}}},config=config)['entries']
    fixed=collect_ledger({'chains':{'ethereum':{'events':[event]}}},config=config,sde_principal=[proof])['entries']
    assert sum(D(r['amount_usd']) for r in original)==sum(D(r['amount_usd']) for r in fixed)==-100
    assert sum(D(r['amount_usd']) for r in fixed if r['line_id']=='sky-direct-deployments')==-40
    assert sum(D(r['amount_usd']) for r in fixed if r['line_id']=='allocation-changes')==-60
    assert all(r['parent'] is None for r in layout() if r['id'] in ('sky-direct-deployments','treasury-disposals'))


def test_curve_dynamic_liquidity_receipt_uses_coin_one_and_ignores_swaps():
    from settle.revenue.spark_sde_cash import curve_receipt, CURVE, DYNAMIC
    tx='0x'+'c'*64;block=100;owner=ALM
    # Dynamic amount array starts at byte offset 128; fee array at 224.
    words=[128,224,1,1,2,0,50*10**6,2,0,0]
    signature=next(k for k,v in DYNAMIC.items() if v==1)
    row=dict(address=CURVE,transactionHash=tx,blockNumber=hex(block),logIndex='0x1',topics=[signature,'0x'+owner[2:].zfill(64)],data='0x'+''.join(f'{n:064x}' for n in words))
    receipt=dict(transactionHash=tx,status='0x1',blockNumber=hex(block),logs=[row])
    assert curve_receipt(receipt,owner,expected_tx=tx,expected_block=block)==(50*10**6,[1])
    wrong=deepcopy(receipt);wrong['status']='0x0'
    import pytest
    with pytest.raises(ValueError):curve_receipt(wrong,owner,expected_tx=tx,expected_block=block)


def test_sde_source_digest_scope_and_duplicates_are_validated():
    import pytest
    from settle.revenue.spark_sde_cash import validate_document, MANAGER
    from settle.revenue.verification import digest
    entry=dict(chain='ethereum',transaction='0x'+'a'*64,venue='S62',counterparty=MANAGER,
               token=USDT,principal_raw='100',receipt_bridge_digest='b'*64,event_indices=[1])
    source=dict(status='available',entries=[entry],errors={},event_digest=digest([entry]))
    validate_document({'sde_cash':source})
    bad=deepcopy(source);bad['entries']=[entry,entry];bad['event_digest']=digest(bad['entries'])
    with pytest.raises(ValueError):validate_document({'sde_cash':bad})


def test_curator_pre_mint_ownership_splits_gross_fees_without_creating_income(monkeypatch):
    from datetime import date
    from types import SimpleNamespace
    from settle.revenue import spark_onchain as onchain
    from settle.extract.transfer_logs import TRANSFER_TOPIC0
    from tests.unit.test_spark_onchain import LogRow
    monkeypatch.setattr(onchain, 'pending_fee', lambda *args: D(0))
    spec = next(s for s in onchain.CURATORS if s[0] == 'S34')
    vault, holder = spec[2], onchain.BASE_ALM
    units=10**12
    def row(index, topic0, data, recipient=None):
        return LogRow(block_number=2,log_index=index,block_time=1,address=vault,topic0=topic0,
            topic1=onchain.ZERO_TOPIC if recipient else None,
            topic2=onchain.topic(recipient) if recipient else None,topic3=None,
            data='0x'+''.join(f'{v:064x}' for v in data),transaction_hash='0x'+'1'*64)
    rows=[row(5,TRANSFER_TOPIC0,[10*units],holder),row(6,onchain.ACCRUE_INTEREST,[1010*10**6,10*units])]
    class Source:
        period=SimpleNamespace(start=date(2026,10,1),end=date(2026,10,1))
        def logs(self,*args):return rows
        def block(self,chain,day):return 1 if day<self.period.start else 2
        def call(self,chain,vault,sig,block):return hex((1000 if block==1 else 1010)*units)
        def number(self,chain,vault,sig,block,*args):return (900 if block==1 else 910)*units
    from decimal import localcontext
    with localcontext() as ctx:
        ctx.prec=100
        result=onchain.curator_fees(Source(),spec,owned_wallets=[holder])
        att=result['attribution']
        assert D(att['self_received_usd']) == D(result['received_usd'])*D('0.9')
        assert D(att['external_accrued_usd'])+D(att['self_rebate_accrued_usd'])==D(result['accrued_usd'])


def test_curator_split_moves_self_fee_to_supply_without_touching_unrelated_amm_components():
    from settle.revenue.spark_curator_attribution import apply
    def row(key,value,**extra):
        return dict(id=key,label=key,aggregation='leaf',values={'2026-10':metric(value)},**extra)
    native=row('investment.venue.S34','90')
    s=dict(periods=[dict(key='2026-10',state='complete',expected_cutoff='2026-10-31')],lines=[native,
        row('fees.curator.S34','10'),row('fees.curator','10',components=[dict(id='fees.curator.S34',sign=1)]),
        row('investment.amm.v4','5',components=[dict(id='opaque_external_capital_proof',sign=1)])])
    source=dict(revision_id='a'*64,cutoff='2026-10-31',document=dict(curator_attribution=dict(S34=dict(attribution=dict(
        status='available',quality='estimate',basis='controlled_ownership',external_accrued_usd='1',self_rebate_accrued_usd='9')))))
    apply(s,[source]);lookup={r['id']:r for r in s['lines']}
    assert D(native['values']['2026-10']['value'])==99
    assert D(lookup['fees.curator']['values']['2026-10']['value'])==1
    assert native['aggregation']=='leaf'
    assert lookup['investment.amm.v4']['values']['2026-10']['value']=='5'


def test_cash_policy_cohorts_cannot_replace_each_other_and_old_selections_replay():
    from datetime import date
    from settle.revenue.cash_flow.project import annual,decode
    from settle.revenue.cash_flow.config import LEGACY_POLICY,REVIEW_POLICY
    from tests.unit.test_cash_flow import publication
    from settle.revenue.verification import digest
    new=publication();old=deepcopy(new)
    old['revision_id']='b'*64;old['policy_version']=LEGACY_POLICY;old['document']['policy_version']=LEGACY_POLICY
    old['document_hash']=digest(old['document'])
    a=annual('spark',2026,date(2026,11,4),[old,new],review=False)
    b=annual('spark',2026,date(2026,11,4),[old,new],review=True)
    assert a['policy_version']==LEGACY_POLICY and a['periods'][9]['revision']==old['revision_id']
    assert b['policy_version']==REVIEW_POLICY and b['periods'][9]['revision']==new['revision_id']
    assert annual('spark',2026,date(2026,11,4),[old,new],selection=decode(a['selection'],'spark',2026))==a
