#!/usr/bin/env python3
"""Identify Spark Savings cash funding separately from Sky draws and revenue.

No ledger mutation: takes are outside saver funding, while returns can include
principal and VSR interest. This audit does not infer a principal/interest split.
"""
import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

from settle.domain.config import load_prime_by_id
from settle.domain.primes import PricingCategory
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0

TAKE = '0x'+keccak256(b'Take(address,uint256)').hex()


def savings_flows(groups):
    prime = load_prime_by_id('spark')
    records = []
    for g in groups:
        chain = g['chain']
        venues = [v for v in prime.venues if v.chain.value == chain
                  and v.pricing_category == PricingCategory.SPARK_SAVINGS_V2
                  and v.underlying.symbol in ('USDC','USDT','PYUSD')]
        if not venues:
            raise ValueError('No configured stablecoin Savings vault on this chain')
        holder = prime.alm[venues[0].chain].hex
        specs = {v.token.address.hex: v for v in venues}
        unique = {}
        for r in g['rows']:
            if r['block_number'] > g['pin']:
                raise ValueError('Savings evidence exceeds the historical pin')
            key = r['transaction_hash'],r['log_index']
            if key in unique and unique[key] != r:
                raise ValueError('Conflicting Savings event')
            unique[key] = r
        takes, cash_in, cash_out = defaultdict(int),defaultdict(int),defaultdict(int)
        blocks = {}
        for r in unique.values():
            if r['topic0'] == TAKE and r['address'] in specs and r['topic1'].endswith(holder[2:]):
                key = r['address'],r['transaction_hash']
                takes[key] += int(r['data'],16)
            elif r['topic0'] == TRANSFER_TOPIC0:
                sender,recipient = '0x'+r['topic1'][-40:],'0x'+r['topic2'][-40:]
                vault = sender if recipient == holder else recipient if sender == holder else None
                if vault not in specs or r['address'] != specs[vault].underlying.address.hex:
                    continue
                key = vault,r['transaction_hash']
                (cash_in if recipient == holder else cash_out)[key] += int(r['data'],16)
                blocks[key] = r['block_number'],r['block_time']
        if dict(takes) != dict(cash_in):
            raise ValueError('Savings Take events differ from actual vault-to-ALM payments')
        for key in sorted(cash_in.keys() | cash_out.keys()):
            vault,tx = key
            v = specs[vault]
            incoming,outgoing = D(cash_in.get(key,0))/10**v.underlying.decimals,D(cash_out.get(key,0))/10**v.underlying.decimals
            records.append({'chain':chain,'transaction_hash':tx,'venue':v.id,'vault':vault,
                'block':blocks[key][0],'timestamp':blocks[key][1],
                'take_to_alm':str(incoming),'return_to_vault':str(outgoing),
                'net_cash_to_alm':str(incoming-outgoing)})
    return records


def summarize(records,finance):
    by_venue = defaultdict(lambda: {'take_to_alm':D(0),'return_to_vault':D(0),'transactions':0})
    by_tx = defaultdict(D)
    for r in records:
        for k in ('take_to_alm','return_to_vault'):
            by_venue[r['venue']][k] += D(r[k])
        by_venue[r['venue']]['transactions'] += 1
        by_tx[r['chain']+':'+r['transaction_hash']] += D(r['net_cash_to_alm'])
    matched = {'unmatched_receipts':{},'unmatched_outflows':{}}
    for field in matched:
        for identity,amount in finance[field].items():
            original = ':'.join(identity.split(':')[:2])
            net = by_tx.get(original,D(0))
            expected = net if field=='unmatched_receipts' else -net
            if expected > 0 and abs(D(str(amount))-expected) <= D('.01'):
                matched[field][identity] = {'raw_residual':amount,'net_savings_cash':str(net)}
    return {'scope':'Cash-flow identification only; no Sky principal, income, cost or interest/principal split inferred.',
        'by_venue':dict(by_venue),
        'whole_transaction_matches':{k:{'count':len(v),'amount':sum((D(str(r['raw_residual'])) for r in v.values()),D(0))}
                                     for k,v in matched.items()},
        'matched':matched, 'flows':records}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('events','financing','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--summary-only',action='store_true')
    args=p.parse_args()
    raw=args.events.read_bytes()
    result=summarize(savings_flows(json.loads(gzip.decompress(raw) if args.events.suffix=='.gz' else raw)),
                     json.loads(args.financing.read_text()))
    result['input_hashes']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.events,args.financing)}
    if args.summary_only:
        result['observed_vault_transactions'] = len(result.pop('flows'))
        result.pop('matched')
    args.output.write_text(json.dumps(result,indent=2,default=str)+'\n')


if __name__=='__main__':
    main()
