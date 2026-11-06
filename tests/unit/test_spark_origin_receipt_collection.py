import importlib
from datetime import date
from pathlib import Path
from types import SimpleNamespace


def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    return importlib.import_module('collect_spark_origin_receipts')


def test_selection_includes_early_month_survivors_and_deduplicates(monkeypatch):
    m = module(monkeypatch)
    monkeypatch.setattr(m, 'audit', lambda h,v,s,e:e)
    old, shared = 'ethereum:0xold', 'ethereum:0xshared'
    def ranked(end, origin, target):
        rows = [(shared,'2000'),('ethereum:0xadapter:route','3000')]
        if end.month == 1:
            rows.append((old,'5000'))
        return [{'transaction':tx,'unclassified_closing_capital':value} for tx,value in rows]
    monkeypatch.setattr(m, 'surviving_sources', ranked)
    result = m.select(SimpleNamespace(venue_accounts={'S32':m.SUSDS}), {},date(2026,1,1),date(2026,2,28))
    assert set(result['transactions']) == {'0xold','0xshared'}
    assert len(result['transactions']['0xold']) == 1
    assert len(result['transactions']['0xshared']) == 2
    assert len(result['monthly_priorities']['2026-01-31']) == 3


def test_fetch_resumes_and_does_not_expose_provider_errors(monkeypatch,tmp_path):
    m=module(monkeypatch)
    calls=[]
    monkeypatch.setattr(m,'rpc_url',lambda chain:'private-provider-url')
    monkeypatch.setattr(m.time,'sleep',lambda delay:None)
    def post(url,method,params):
        calls.append(params[0])
        if params[0]=='0xbad':
            raise RuntimeError('credential-bearing provider URL')
        return {'transactionHash':params[0],'status':'0x1'}
    monkeypatch.setattr(m,'_post',post)
    cache={'0xexisting':{'unchanged':True}}
    path=tmp_path/'cache.json.gz'
    failures=m.fetch_missing({'transactions':{'0xexisting':[],'0xnew':[],'0xbad':[]}},cache,path)
    assert '0xexisting' not in calls
    assert calls.count('0xbad')==3
    assert m.read(path)==cache
    assert failures==[{'transaction':'0xbad','error_type':'RuntimeError'}]
    calls.clear()
    assert m.fetch_missing({'transactions':{'0xexisting':[],'0xnew':[]}},cache,path)==[]
    assert calls==[]
