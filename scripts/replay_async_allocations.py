"""Read-only Jan–Sep component replay. No monthly settlements or publications.

Reuses production capital, exposure and revenue functions with retained public
inputs, and rejects network calls/missing inputs. See the accompanying README.
"""
import argparse
import hashlib
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--evidence-dir', type=Path, default=Path('reconciliation/async-allocations-2026-01_to_2026-09'))
parser.add_argument('--baseline', default='9b290a3f164882f2e19cad9fbc7ad597bce400a7')
parser.add_argument('--from-month', default='2026-01')
parser.add_argument('--to-month', default='2026-09')
parser.add_argument('--primes', nargs='+', default=['spark','obex'], choices=['spark','obex'])
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
if args.baseline != '9b290a3f164882f2e19cad9fbc7ad597bce400a7' or (args.from_month,args.to_month)!=('2026-01','2026-09'):
    parser.error('Retained audit inputs cover only the stated baseline and Jan–Sep 2026')
EVIDENCE=args.evidence_dir
for name,digest in __import__('json').loads((EVIDENCE/'sha256.json').read_text()).items():
    if hashlib.sha256((EVIDENCE/name).read_bytes()).hexdigest()!=digest:
        raise ValueError('Retained evidence hash mismatch: '+name)

import json,gzip,calendar
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch
from settle.domain.config import load_prime_by_id
from settle.domain.period import Period
from settle.extract import rpc,hypersync_store
from settle.extract.hypersync import LogRow
from settle.normalize import async_capital as ac
h=json.load(gzip.open(EVIDENCE/'history.json.gz'));q=json.load(gzip.open(EVIDENCE/'state-quotes.json.gz'))
rows=[LogRow(**r) for n,g in h['queries'].items() for r in g['rows']]
def fetch(chain,selections,start,pin,**kw):
 def match(r,s):
  return (not s.get('address') or r.address in s['address']) and all(not t or getattr(r,'topic'+str(i)) in t for i,t in enumerate(s.get('topics',[])))
 def covers(s, full):
  if not set(s.get('address',[])).issubset(full.get('address',[])):return False
  for i,allowed in enumerate(full.get('topics',[])):
   chosen=s.get('topics',[])[i] if i<len(s.get('topics',[])) else []
   if allowed and (not chosen or not set(chosen).issubset(allowed)):return False
  return True
 expanded=[]
 for s in selections:
  for t in s.get('topics',[[]])[0] or [None]:
   x=dict(s);x['topics']=[[t] if t else [],*s.get('topics',[])[1:]];expanded.append(x)
 for s in expanded:
  if not any(g['from_block']<=start and g['to_block']>=pin and g['archive_height']>=pin
             and any(covers(s,full) for full in g['selections']) for g in h['queries'].values()):
   raise ValueError('Incomplete retained log query coverage')
 return [r for r in rows if start<=r.block_number<=pin and any(match(r,s) for s in selections)]
def post(url,method,params):
 key=json.dumps([method,params],sort_keys=True,separators=(',',':'))
 if key not in q['rpc']:raise ValueError('Unrecorded RPC '+method+' '+str(params))
 return q['values'][q['rpc'][key]]
class Resolver:
 def block_at_or_before(self,chain,dt):return q['boundaries'][dt.date().isoformat()]

from dataclasses import replace
from datetime import UTC,datetime,timedelta
from pathlib import Path
import pandas as pd
import openpyxl
from settle.normalize.positions import _shares_to_usd_inflow_timeseries
from settle.compute.prime_agent_revenue import compute_venue_revenue,VenueRevenueInputs
from settle.normalize.async_vaults import async_capital_inflow_timeseries,AsyncVaultExposure
from settle.revenue.verification import canonical
class Balance:
 def directed_inflow_timeseries(self,*,chain,token,from_addr,to_addr,start,pin_block):
  out={}
  for r in rows:
   if r.address=='0x'+token.hex() and r.topic0==ac.TRANSFER_TOPIC0 and r.topic1=='0x'+from_addr.hex().zfill(64) and r.topic2=='0x'+to_addr.hex().zfill(64) and r.block_number<=pin_block:
    d=datetime.fromtimestamp(r.block_time,UTC).date()
    if d>=start:out[(r.block_number,r.log_index)]=(d,D(int(r.data,16))/10**6)
  byday={}
  for d,x in out.values():byday[d]=byday.get(d,D(0))+x
  return pd.DataFrame([dict(block_date=d,daily_inflow=x) for d,x in sorted(byday.items())],columns=['block_date','daily_inflow'])
class Provider:
 def capital_events(self,*args,**kwargs):return ac.capital_events(*args,**kwargs)
result=[]
with patch.object(rpc,'_post',post),patch.object(rpc,'rpc_url',lambda chain:'recorded'),patch.object(hypersync_store,'fetch_logs',fetch),patch.object(ac,'receipt',lambda chain,tx:h['maple_return_receipts'][tx]):
 for name,vid in [('spark','S14'),('spark','S15'),('obex','V1')]:
  if name not in args.primes:continue
  p=load_prime_by_id(name);v=next(v for v in p.venues if v.id==vid);old=replace(v,async_allocation=None,share_burn_destinations=[] if name=='obex' else v.share_burn_destinations)
  prices={b:D(q['prices'][d+'/'+v.token.address.hex]) for d,b in q['boundaries'].items()}
  owner=ac.who(p.alm[v.chain]);escrows={m.address.hex for m in v.async_allocation.managers}
  relevant=ac.unique_rows([r for r in rows if r.address==v.token.address.hex and r.topic0==ac.TRANSFER_TOPIC0 or r.address in escrows and r.topic2==owner],h['pin'])
  timeline=[];wallet=pending=0
  legacy={ac.topic(sig.replace('uint256,','uint128,',1)):ac.topic(sig) for sig in ['RequestCreated(uint256,address,uint256)','RequestProcessed(uint256,address,uint256,uint256)','ManualSharesIncreased(uint256,address,uint256)']}
  manual_txs={(r.transaction_hash,r.topic1) for r in relevant if legacy.get(r.topic0,r.topic0)==ac.topic('ManualSharesIncreased(uint256,address,uint256)')}
  for r in relevant:
   t=legacy.get(r.topic0,r.topic0)
   if r.address==v.token.address.hex:
    if r.topic2==owner:wallet+=int(r.data,16)
    if r.topic1==owner:wallet-=int(r.data,16)
    if r.topic2==owner and '0x'+r.topic1[-40:] in escrows:pending-=int(r.data,16)
   elif t==ac.topic('RequestCreated(uint256,address,uint256)'):pending+=int(r.data,16)
   elif t==ac.topic('RequestProcessed(uint256,address,uint256,uint256)') and (r.transaction_hash,r.topic1) not in manual_txs:pending-=ac.words(r,2)[0]
   timeline.append((r.block_number,wallet,pending))
  def state(b):
   rr=[x for x in timeline if x[0]<=b]
   return rr[-1][1:] if rr else (0,0)
  for m in range(1,10):
   start=date(2026,m,1);end=date(2026,m,calendar.monthrange(2026,m)[1]);op=q['boundaries'][(start-timedelta(days=1)).isoformat()];pin=q['boundaries'][end.isoformat()];period=Period(start,end,{v.chain:pin})
   for day,b in [(start-timedelta(days=1),op),(end,pin)]:
    proof=q['states'][f'{day}/{name}/{vid}'];w,pdng=state(b)
    assert (str(w),D(pdng)/10**6)==(proof['wallet_raw'],D(proof['exposure']['redemption_shares'])),(name,vid,day,w,pdng,proof)
   def raw(v0):return _shares_to_usd_inflow_timeseries(p,v0,period,balance_source=Balance(),block_resolver=Resolver(),price_at_block=lambda b:prices[b],som_block=op,balance_at=lambda *args:state(args[-1])[0],period_only=True)
   before=raw(old);afterraw=raw(v);after=async_capital_inflow_timeseries(p,v,period,afterraw,opening_block=op,block_resolver=Resolver(),nav_at_block=lambda b:prices[b])
   vs=D(state(op)[0])/10**6*prices[op];ve=D(state(pin)[0])/10**6*prices[pin]
   daily=[];windows=[]
   for d in [start+timedelta(days=i) for i in range(period.n_days)]:
    b=q['boundaries'][str(d)];w,pdng=state(b);nv=prices[b]
    daily.append(dict(block_date=d,economic_value=D(w+pdng)/10**6*nv,wallet_value=D(w)/10**6*nv,pending_value=D(pdng)/10**6*nv))
    if pdng:windows.append(dict(date=str(d),block=b,pending_shares=str(D(pdng)/10**6),pending_value=str(D(pdng)/10**6*nv)))
   oldr=compute_venue_revenue(period,VenueRevenueInputs(venue=old,value_som=vs,value_eom=ve,inflow_timeseries=before))
   newr=compute_venue_revenue(period,VenueRevenueInputs(venue=v,value_som=vs,value_eom=ve,inflow_timeseries=after,economic_value_timeseries=pd.DataFrame(daily)))
   f=next(Path(f'settlements/{name}/2026-{m:02}').glob('*.xlsx'));wb=openpyxl.load_workbook(f,read_only=True,data_only=True);rr=list(wb['Venues'].values);header=rr[0];record=next(dict(zip(header,x)) for x in rr[1:] if x[0]==vid);wb.close()
   record={k:str(x) for k,x in record.items() if x is not None}
   out=dict(prime=name,venue=vid,month=f'2026-{m:02}',opening_block=op,pin=pin,old=canonical(oldr),new=canonical(newr),income_delta=str(newr.actual_revenue-oldr.actual_revenue),prior_async_credit='0',net_income_delta=str(newr.actual_revenue-oldr.actual_revenue),global_cof_delta='0',published=record,published_workbook_sha256=hashlib.sha256(f.read_bytes()).hexdigest(),unpaid_daily_closes=windows,capital_events=canonical(after.attrs['async_capital_events']))
   result.append(out);print(name,vid,m,out['income_delta'],'published',record['actual_revenue'],'old',oldr.actual_revenue,flush=True)
# Full earlier history seeds the zero opening claims. Empty month-end
# wallets alone would not prove the absence of intramonth async activity.
if 'spark' in args.primes:
 from settle.normalize import issuer_redemptions as issuer
 from settle.compute import spark_ustb_capital, spark_uscc_capital
 from settle.normalize.async_vaults import async_vault_exposure
 spark=load_prime_by_id('spark')
 quote_evidence=json.loads((EVIDENCE/'issuer-quotes.json').read_text())
 def issue_quote(v,b):return D(quote_evidence[v.token.address.hex+'/'+str(b)]['price'])
 with patch.object(rpc,'_post',post),patch.object(rpc,'rpc_url',lambda chain:'recorded'),patch.object(hypersync_store,'fetch_logs',fetch),patch.object(issuer,'quote',issue_quote):
  # replay's default callable is explicit to avoid Python default binding.
  for vid in ['S16','S19','S21','S22']:
   v=next(v for v in spark.venues if v.id==vid)
   source_names=['ethena-withdrawals','ethena-cash','ethena-owner-shares'] if vid=='S16' else ['spark-rwa-shares','spark-issuer-cash']
   source=[r for name in source_names for r in h['queries'][name]['rows']]
   if vid!='S16':
    policy=issuer.policy_for(spark,v)
    selected=[r for r in source if r['address']==v.token.address.hex or r['address']==policy['cash_token'] and r['topic1'].endswith(policy['cash_payer'][2:])]
   else:selected=source
   assert not any(1767225600<=r['block_time']<=1790812799 for r in selected), 'Unreviewed 2026 issuer/cooldown activity'
   for day in ['2025-12-31','2026-09-30']:
    proof=q['states'][f'{day}/spark/{vid}'];assert int(proof['wallet_raw'])==0
    if vid=='S16':
     assert D(proof['exposure']['redemption_cash'])==0
    else:
     per=Period(date.fromisoformat(day),date.fromisoformat(day),{v.chain:proof['block']})
     ev=issuer.source_events(spark,v,per,policy)
     assert not issuer.replay(v,policy,ev,pin=proof['block'],price_at=issue_quote)[0]
   for m in range(1,10):
    result.append(dict(prime='spark',venue=vid,month=f'2026-{m:02}',income_delta='0',net_income_delta='0',prior_async_credit='0',global_cof_delta='0',opening_value='0',closing_value='0',average_value='0',unpaid_daily_closes=[],proof='Complete owner source history, paid 2025 requests and empty pinned opening/closing state'))
 # Positive evidence for Arkis atomic redemptions; exact same-transaction
 # underlying delivery, not inference from zero month-end holdings.
 grouped={}
 for r in h['queries']['arkis-atomic-withdrawals']['rows']:grouped.setdefault(r['transaction_hash'],[]).append(r)
 for tx,rr in grouped.items():
  requested=sum(ac.words(LogRow(**r),2)[0] for r in rr if r['topic0']==ac.topic('Withdraw(address,address,address,uint256,uint256)'))
  delivered=sum(ac.words(LogRow(**r),1)[0] for r in rr if r['topic0']==ac.TRANSFER_TOPIC0)
  assert requested==delivered and requested>0

args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(dict(baseline=args.baseline,results=result),indent=2)+'\n')
