"""Read-only historical principal estimate; never runs or publishes settlements.

Uses Mint(value - balanceIncrease), Burn(-(value + balanceIncrease)), and
BalanceTransfer(scaledValue * emittedIndex). Compares to frozen provenance.
This is an economic revenue estimate, not a full replay of PR #220 or payout.
"""
import argparse
import json
from dataclasses import asdict
from decimal import Decimal, localcontext
from pathlib import Path

from settle.domain.config import load_prime_by_id
from settle.domain.primes import PricingCategory
from settle.extract import aave_reconstruct as aave
from settle.extract.hypersync import query_logs


def principal_raw(row, holder):
    words = aave._words(row.data)
    if row.topic0 == aave.MINT_T0:
        return Decimal(words[0] - words[1])
    if row.topic0 == aave.BURN_T0:
        return -Decimal(words[0] + words[1])
    if row.topic0 == aave.BT_T0:
        direction = int(row.topic2 == holder) - int(row.topic1 == holder)
        return Decimal(direction * words[0]) * Decimal(words[1]) / Decimal(aave.RAY)
    raise ValueError('Unexpected event')


def run(root, output, first, last):
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for name in ('spark', 'grove', 'osero'):
        prime = load_prime_by_id(name)
        docs = []
        for path in sorted((root/name).glob('*/provenance.json')):
            if first <= path.parent.name <= last:
                docs.append(json.loads(path.read_text()))
        for venue in prime.venues:
            if venue.pricing_category not in {PricingCategory.AAVE_ATOKEN, PricingCategory.SPARKLEND_SPTOKEN}:
                continue
            chain = venue.chain.value
            periods = [(doc, next((v for v in doc['venue_breakdown'] if v['venue_id']==venue.id), None)) for doc in docs]
            periods = [(d,v) for d,v in periods if v is not None and chain in d['pin_blocks_som']]
            if not periods:
                continue
            lo = min(d['pin_blocks_som'][chain] for d,v in periods)
            hi = max(d['pin_blocks_eom'][chain] for d,v in periods)
            holder = aave._addr_topic((venue.holder_override or prime.alm[venue.chain]).value)
            token = '0x'+venue.token.address.value.hex()
            selections = [
                {'address':[token], 'topics':[[aave.MINT_T0], [], [holder]]},
                {'address':[token], 'topics':[[aave.BURN_T0], [holder]]},
                {'address':[token], 'topics':[[aave.BT_T0], [holder]]},
                {'address':[token], 'topics':[[aave.BT_T0], [], [holder]]},
            ]
            cache = output/f'{name}-{venue.id}-events.json'
            # Always fetch the requested pinned range; never reuse an unkeyed
            # prior output from a different holder, config or date window.
            rows = query_logs(chain, selections, lo+1, hi).rows
            cache.write_text(json.dumps({
                'chain': chain, 'selections': selections, 'from_block': lo+1,
                'to_block': hi, 'events': [asdict(r) for r in rows],
            }, indent=2)+'\n')
            rows = list({(r.block_number,r.log_index):r for r in rows}.values())
            for doc, published in periods:
                lower,upper = doc['pin_blocks_som'][chain],doc['pin_blocks_eom'][chain]
                events = [r for r in rows if lower<r.block_number<=upper]
                with localcontext() as ctx:
                    ctx.prec=78
                    capital = sum((principal_raw(r,holder) for r in events),Decimal(0))/10**venue.token.decimals
                    correction = Decimal(published['period_inflow'])-capital
                    result = {'prime':name,'month':doc['month'],'venue':venue.id,'chain':chain,'token':token,
                              'holder':holder,'som':lower,'eom':upper,'events':len(events),
                              'published_capital':published['period_inflow'],'event_capital':str(capital),
                              'published_actual_revenue':published['actual_revenue'],
                              'estimated_actual_revenue':str(Decimal(published['actual_revenue'])+correction),
                              'revenue_correction':str(correction),'sd_share':published.get('sd_share','0')}
                results.append(result)
            print(name,venue.id,'events',len(rows),flush=True)
    totals = {}
    for r in results:
        key = r['prime']+'/'+r['month']
        totals[key] = totals.get(key,Decimal(0))+Decimal(r['revenue_correction'])
    report = {'method':'Event principal compared with published inflow; other components held fixed; not a canonical settlement replay',
              'first':first,'last':last,'totals':{k:str(v) for k,v in sorted(totals.items())},'venues':results}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report['totals'],indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settlements',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--first',default='2026-01')
    parser.add_argument('--last',default='2026-08')
    args=parser.parse_args()
    run(args.settlements,args.output,args.first,args.last)
