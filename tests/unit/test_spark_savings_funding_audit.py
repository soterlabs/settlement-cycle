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


def test_sky_draw_replenishes_saver_vault_instead_of_buying_a_new_alm_asset():
    rows=json.loads((ROOT/'tests/fixtures/spark_savings_refinancing_may18.json').read_text())
    transfers=[r for r in rows if r['topic0']==audit.TRANSFER_TOPIC0]
    alm='1601843c5e9bc251a3272907010afa41fa18347e'
    usds='0xdc035d45d973e3ec169d2276ddab16f1e407384f'
    usdc='0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
    vault='28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d'
    assert any(r['address']==usds and r['topic1'].endswith('c395d150e71378b47a1b8e9de0c1a83b75a08324')
               and r['topic2'].endswith(alm) and int(r['data'],16)==399989732847945526048219637 for r in transfers)
    assert any(r['address']==usdc and r['topic1'].endswith(alm)
               and r['topic2'].endswith(vault) and int(r['data'],16)==399989732847945 for r in transfers)
    assert any(r['address']==usdc and r['topic1'].endswith(vault)
               and r['topic2'].endswith('d00e0079b8cab524f3fa20ea879a7736e512a5fc')
               and int(r['data'],16)==399960824213788 for r in transfers)
    assert not any(r['address']=='0x'+vault and r['topic0']==audit.TAKE for r in rows)
    # The audit identifies the actual return; it must not fabricate an ERC4626
    # investment/share receipt for the ALM from this funding transaction.
    assert not any(r['address']=='0x'+vault and r['topic2'].endswith(alm) for r in transfers)
