from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from settle.api.app import create_app
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_morpho_fees import ACCRUE_INTEREST, ACCRUE_INTEREST_V2, ZERO_TOPIC
from settle.revenue import spark_onchain
from settle.revenue.financial import decode_selection, statements
from settle.revenue.spark_onchain import CURATORS, pending_fee, revaluation
from settle.revenue.spark_reporting_job import month_dates
from settle.revenue.verification import digest
from tests.unit.test_financial_spark import saved


def supplement(cutoff='2026-10-26', *, fees='2'):
    document = {'schema_version': 'spark-onchain.v1', 'period_start': cutoff[:7] + '-01', 'cutoff': cutoff,
        'reserve_fees': {'status': 'available', 'amounts': dict.fromkeys(('usds','usdc','usdt','dai','pyusd','rlusd','usdg'), '2'), 'unknown_tokens': []},
        'curator_fees': {venue: {'status': 'available', 'accrued_usd': fees, 'received_usd': '1', 'opening_pending_usd': '0', 'closing_pending_usd': str(D(fees)-1)} for venue,*_ in CURATORS},
        'gains': {asset: {'status': 'available', 'value': '3', 'quality': 'estimate', 'basis': 'net_asset_revaluation'}
                  for asset in ('morpho','avax','eth_weth','sparklend_nonstable')},
        'native_supplies': {'S14': {'status': 'available', 'value': '10', 'average_position_usd': '1000', 'quality': 'estimate'}}}
    ref = digest(document)
    return {'revision_id': ref, 'publication_order': 1, 'cutoff': cutoff, 'document': document,
            'source': {'kind': 'spark_onchain_components', 'revision_id': ref, 'result_hash': digest(document), 'currency': 'USD', 'cutoff': cutoff}}


def test_revaluation_nets_liabilities_and_never_books_deposits_or_index_yield_as_price_gain():
    hedged = revaluation([(D(100),D(100),D(2000)),(D(150),D(150),D(2200))])
    assert D(hedged['value']) == 0 and hedged['quality'] == 'supported'
    flat = revaluation([(D(0),D(0),D(2000)),(D(150),D(0),D(2000))])
    assert D(flat['value']) == 0  # Capital/interest units are not price gains.
    own = revaluation([(D(120),D(100),D(2000)),(D(120),D(100),D(2100))])
    assert D(own['value']) == 2000 and D(own['depositor_offset_usd']) == 10000
    assert own['quality'] == 'estimate'


def test_pending_v1_uses_the_vaults_own_fee_dilution_instead_of_an_assumed_apr():
    class Source:
        def call(self, chain, vault, signature, block, *args):
            return '0x'+ ('0'*24+'1601843c5e9bc251a3272907010afa41fa18347e') if signature=='feeRecipient()' else hex(1000)
        def number(self, chain, vault, signature, block, *args):
            return {'totalSupply()':100, 'convertToShares(uint256)':111, 'convertToAssets(uint256)':9}[signature]
    assert pending_fee(Source(), CURATORS[3], 1) == D(9)/10**18


def test_monthly_components_work_without_native_history_and_saved_selections_replay():
    record = supplement()
    report = statements('spark',2026,date(2026,10,26),[],spark_income=True,spark_publications=[record])
    rows={row['id']:row for row in report['lines']}
    assert rows['fees.curator']['values']['2026-10']['value'] == '12'
    assert rows['fees.sparklend']['values']['2026-10']['value'] == '14'
    assert rows['investment.venue.S14']['values']['2026-10']['value'] == '10'
    assert rows['investment.maple_syrup_usdc']['values']['2026-10']['value'] == '10'
    assert rows['investment.venue.S14']['values']['2026-10']['quality'] == 'estimate'
    assert rows['fees.curator']['values']['2026-10']['recognition'] == 'accrual'
    selection=decode_selection(report['selection'],'spark',2026)
    newer=supplement(fees='99')
    newer['publication_order']=2
    assert statements('spark',2026,date(2026,10,26),[],selection=selection,spark_publications=[record,newer]) == report
    partial=statements('spark',2026,date(2026,10,27),[],spark_income=True,spark_publications=[record])
    metric=next(row for row in partial['lines'] if row['id']=='fees.curator')['values']['2026-10']
    assert metric['value'] is None and metric['known_value']=='12'
    assert month_dates(date(2026,1,1),date(2026,3,2)) == [date(2026,1,31),date(2026,2,28),date(2026,3,2)]


def test_monthly_endpoints_return_accrual_and_one_native_venue_without_deducting_global_funding(monkeypatch):
    from settle.api import revenue
    monkeypatch.setattr(revenue,'today',lambda:date(2026,10,27))
    class Reader:
        def financial_year(self,*args,**kwargs): return [saved()]
        def spark_component_year(self,*args,**kwargs): return [supplement()]
    client=TestClient(create_app(reader=Reader()))
    fees=client.get('/v3/reporting/fees?prime=spark&month=2026-10&venue=S10')
    assert fees.status_code==200,fees.text
    assert len(fees.json()['curator_fees'])==1
    assert list(fees.json()['metrics'])==['fees.curator.S10']
    native=client.get('/v3/reporting/allocations?prime=spark&month=2026-10&venue=S14')
    assert native.status_code==200,native.text
    assert native.json()['count']==1 and not native.json()['funding_cost_deducted']
    assert not native.json()['external_rewards_included']
    assert client.get('/v3/reporting/fees?prime=spark&month=0000-01').status_code==422
    assert client.get('/v3/reporting/gains?prime=spark&month=2026-12').status_code==422


@pytest.mark.parametrize('v2,expected', [(False, '0.000000000000000099'), (True, '165.289256')])
def test_fee_event_nav_accounts_for_both_fee_recipients_and_reconciles_supply(monkeypatch, v2, expected):
    monkeypatch.setattr(spark_onchain, 'pending_fee', lambda *args: D(0))
    spec = CURATORS[1] if v2 else CURATORS[3]
    vault = spec[2]
    scale = 10**12 if v2 else 1
    def row(index, topic0, data, recipient=None):
        return LogRow(block_number=2, log_index=index, block_time=1, address=vault, topic0=topic0,
            topic1=ZERO_TOPIC if recipient else None,
            topic2=spark_onchain.topic(recipient) if recipient else None, topic3=None,
            data='0x'+''.join(f'{x:064x}' for x in data), transaction_hash='0x'+'1'*64)
    if v2:
        rows=[row(5, ACCRUE_INTEREST_V2, [1000*10**6,2000*10**6,10*scale,10*scale]),
              row(6,TRANSFER_TOPIC0,[10*scale],spark_onchain.ETH_ALM),
              row(7,TRANSFER_TOPIC0,[10*scale],'0x'+'2'*40)]
        closing=120*scale
    else:
        rows=[row(5,TRANSFER_TOPIC0,[10],spark_onchain.ETH_ALM), row(6,ACCRUE_INTEREST,[1100,10])]
        closing=110
    class Source:
        period=SimpleNamespace(start=date(2026,10,1),end=date(2026,10,1))
        def logs(self,*args): return rows
        def block(self,chain,day): return 1 if day < self.period.start else 2
        def call(self,chain,vault,sig,block): return hex(100*scale if block==1 else closing)
    result=spark_onchain.curator_fees(Source(),spec)
    assert D(result['received_usd']) == D(expected) and D(result['accrued_usd']) == D(expected)
    closing+=1
    with pytest.raises(ValueError,match='closing state'):
        spark_onchain.curator_fees(Source(),spec)


def test_component_errors_do_not_become_zero_or_leak_exception_values():
    class Broken:
        def __getattr__(self,name): raise RuntimeError('private-value-must-not-escape')
    period=SimpleNamespace(start=date(2026,10,1),end=date(2026,10,31))
    result=spark_onchain.collect(None,period,{},source=Broken())
    assert result['reserve_fees']['status']=='unavailable'
    assert all(row['status']=='unavailable' for row in result['gains'].values())
    assert 'private-value' not in str(result)


def test_failed_refresh_retains_same_month_amount_with_original_coverage():
    from settle.revenue.spark_reporting_store import carry_forward, validate_document
    old = supplement('2026-10-25')
    fresh = supplement('2026-10-26')['document']
    fresh['curator_fees']['S10'] = {'status': 'unavailable'}
    carried = carry_forward(fresh, old)
    row = carried['curator_fees']['S10']
    assert row['status'] == 'partial' and row['period_end'] == '2026-10-25'
    assert row['accrued_usd'] == '2' and row['retained_source_revision'] == old['revision_id']
    validate_document(carried)
    next_month = supplement('2026-11-01')['document']
    next_month['curator_fees']['S10'] = {'status': 'unavailable'}
    assert carry_forward(next_month, old)['curator_fees']['S10']['status'] == 'unavailable'
    carried['curator_fees']['S10']['accrued_usd'] = '999'
    with pytest.raises(ValueError, match='Unreconciled'):
        validate_document(carried)


@pytest.mark.parametrize('candidate,expected', [(100,100),(99,101),(1000,101)])
def test_boundary_uses_fast_candidate_only_after_fresh_exact_finality_validation(monkeypatch,candidate,expected):
    from datetime import UTC, datetime, time
    day=date(2026,1,31)
    target=int(datetime.combine(day,time.max,UTC).timestamp())
    monkeypatch.setattr(spark_onchain.hypersync,'find_block_at_or_before',lambda *a:candidate)
    monkeypatch.setattr(spark_onchain.hypersync,'archive_height',lambda *a:1000)
    monkeypatch.setattr(spark_onchain.hypersync,'block_timestamp_uncached',lambda chain,b:target+(b-100)*2)
    monkeypatch.setattr(spark_onchain.hypersync,'find_finalized_block_at_or_before',lambda *a:101)
    assert spark_onchain.certified_boundary.__wrapped__('ethereum',day,10)==expected


def test_settlement_trueup_difference_is_not_added_to_monthly_receipt_income():
    record=supplement()
    source=saved()
    for key,value in source['document']['metrics'].items():
        if key.startswith('rewards.lending.sparklend.'):
            value.update(value='100',status='available')
    report=statements('spark',2026,date(2026,10,26),[source],spark_income=True,spark_publications=[record])
    residual=next(row for row in report['lines'] if row['id']=='rewards.unattributed_sparklend_receipts')['values']['2026-10']
    assert residual['value'] is None and residual['known_value'] is None


def test_component_log_reads_reuse_the_finalized_coverage_store(monkeypatch):
    source=object.__new__(spark_onchain.Source)
    source.period=SimpleNamespace(start=date(2026,10,1),end=date(2026,10,31))
    source.block=lambda chain,day:100 if day<source.period.start else 200
    expected=[object()]
    def fetch(chain,selections,start,end,*,log_fields):
        assert (chain,start,end)==('ethereum',101,200)
        assert 'transaction_hash' in log_fields
        return expected
    monkeypatch.setattr(spark_onchain,'fetch_logs',fetch)
    assert source.logs('ethereum',[]) is expected
