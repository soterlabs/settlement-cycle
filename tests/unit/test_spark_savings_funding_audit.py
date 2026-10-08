import gzip
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location('savings_audit', ROOT/'scripts/audit_spark_savings_funding.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
EVENTS = json.loads(gzip.decompress((ROOT/'tests/fixtures/spark_savings_v2_funding.json.gz').read_bytes()))


def test_all_takes_match_actual_underlying_transfers_and_chain_specific_vaults():
    records = audit.savings_flows(EVENTS)
    summary = audit.summarize(records,{'unmatched_receipts':{},'unmatched_outflows':{}})
    assert len(records) == 20525
    assert summary['by_venue']['S57']['take_to_alm'] == audit.D('13276235391.443105')
    assert summary['by_venue']['S57']['return_to_vault'] == audit.D('12938428813.371124')
    assert summary['by_venue']['S60']['take_to_alm'] == audit.D('713217978.696943')
    assert set(summary['by_venue']) == {'S56','S57','S59','S60'}


def test_missing_cash_leg_fails_even_if_take_event_exists():
    broken = deepcopy(EVENTS)
    rows=broken[0]['rows']
    take=next(r for r in rows if r['topic0']==audit.TAKE)
    broken[0]['rows']=[r for r in rows if not (r['transaction_hash']==take['transaction_hash']
                                               and r['topic0']==audit.TRANSFER_TOPIC0)]
    with pytest.raises(ValueError,match='actual vault-to-ALM'):
        audit.savings_flows(broken)


def test_mixed_transaction_is_not_claimed_as_wholly_explained():
    records=[{'chain':'ethereum','transaction_hash':'0xabc','venue':'S57',
              'take_to_alm':'100','return_to_vault':'0','net_cash_to_alm':'100'}]
    finance={'unmatched_receipts':{'ethereum:0xabc':'101'},'unmatched_outflows':{}}
    assert audit.summarize(records,finance)['whole_transaction_matches']['unmatched_receipts']['count']==0
    finance['unmatched_receipts']['ethereum:0xabc']='100'
    assert audit.summarize(records,finance)['whole_transaction_matches']['unmatched_receipts']['count']==1
