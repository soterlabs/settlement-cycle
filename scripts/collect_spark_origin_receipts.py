#!/usr/bin/env python3
"""Select sUSDS origin evidence across month ends, not only August survivors.

Ranks the remaining contribution at each cutoff, deduplicates transactions,
and optionally fetches missing Ethereum receipts into a resumable cache.
Selection is research prioritization, never proof of borrowing. The existing
ordered receipt validator must accept each route before origin is attributed.
"""
import argparse
import gzip
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_allocation_positions import audit
from audit_bridged_allocation_origins import surviving_sources
from audit_scoped_allocation_origin import month_ends
from audit_spark_ordered_draws import SUSDS

from settle.compute.executed_spell_capital import apply_executed_spells
from settle.domain.primes import Chain
from settle.extract.rpc import _post, rpc_url
from settle.normalize.allocation_history_cache import load_history


def select(history, origin, start, end, *, limit=25, minimum=D(1000)):
    if limit <= 0 or not minimum.is_finite() or minimum < 0 or end < start:
        raise ValueError('Invalid selection bounds')
    if history.venue_accounts.get('S32') != SUSDS:
        raise ValueError('Expected Spark sUSDS allocation')
    with localcontext() as ctx:
        ctx.prec = 60
        selected, months = {}, {}
        for cutoff in month_ends(start, end):
            local = audit(history, 'S32', start, cutoff)
            ranked = surviving_sources(local, origin, SUSDS)
            # Keep non-raw/adapter identities in the research plan, but do not
            # send them as RPC hashes or count custody aliases as new txs.
            months[str(cutoff)] = ranked[:limit]
            for row in ranked[:limit]:
                identity = row['transaction']
                if (identity.startswith('ethereum:') and identity.count(':') == 1
                        and D(row['unclassified_closing_capital']) >= minimum):
                    selected.setdefault(identity.split(':')[1], []).append({
                        'month_end': str(cutoff),
                        'surviving_unclassified_capital': row['unclassified_closing_capital']})
        return {'scope': __doc__.strip(), 'start': str(start), 'end': str(end),
                'limit_per_month': limit, 'minimum_usd': str(minimum),
                'monthly_priorities': months, 'transactions': selected}


def read(path):
    p = Path(path)
    return json.loads(gzip.decompress(p.read_bytes()) if p.suffix == '.gz' else p.read_bytes())


def save(path, data):
    encoded = (json.dumps(data, indent=2)+'\n').encode()
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_bytes(gzip.compress(encoded,mtime=0) if path.suffix == '.gz' else encoded)
    temporary.replace(path)


def fetch_missing(plan, cache, output):
    url = rpc_url(Chain.ETHEREUM)

    def fetch(tx):
        for attempt in range(3):
            try:
                receipt = _post(url, 'eth_getTransactionReceipt', [tx])
                if not receipt or receipt['transactionHash'].lower() != tx or int(receipt['status'],16) != 1:
                    raise ValueError('Missing or mismatched successful receipt')
                return tx, receipt
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(attempt+1)

    missing = sorted(set(plan['transactions'])-cache.keys())
    failures = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(fetch,tx):tx for tx in missing}
        for n, future in enumerate(as_completed(jobs),1):
            try:
                tx, receipt = future.result()
                cache[tx] = receipt
            except Exception as exc:
                # Provider exceptions may include credential-bearing URLs.
                failures.append({'transaction': jobs[future], 'error_type': type(exc).__name__})
            if n % 10 == 0:
                save(output,cache)
                print('Fetched/attempted:',n,'of',len(missing),flush=True)
    save(output,cache)
    return failures


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history',type=Path,required=True)
    p.add_argument('--origin-report',type=Path,required=True)
    p.add_argument('--start',type=date.fromisoformat,required=True)
    p.add_argument('--end',type=date.fromisoformat,required=True)
    p.add_argument('--limit',type=int,default=25)
    p.add_argument('--minimum',type=D,default=D(1000))
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--receipts',type=Path,required=True,help='Resumable output cache; existing entries are retained.')
    p.add_argument('--fetch',action='store_true')
    args=p.parse_args()
    paths=[args.history,args.origin_report,args.plan,args.receipts]
    if len({x.resolve() for x in paths}) != len(paths):
        p.error('History, report, plan and cache must be distinct files')
    with gzip.open(args.history,'rt') as f:
        key=json.loads(next(f))['fingerprint']
    h=load_history(args.history,key)
    if h is None:
        raise ValueError('Invalid normalized history')
    h=apply_executed_spells(h)
    source=read(args.origin_report)
    if source.get('end','') < str(args.end):
        p.error('Origin report must cover every selected cutoff')
    plan=select(h,source['origin_trace'],args.start,args.end,limit=args.limit,minimum=args.minimum)
    plan['input_sha256']={str(path):hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (args.history,args.origin_report)}
    if args.fetch:
        plan['fetch_failures']=fetch_missing(plan,read(args.receipts) if args.receipts.exists() else {},args.receipts)
    save(args.plan,plan)
    print('Selected',len(plan['transactions']),'unique Ethereum transactions.')


if __name__ == '__main__':
    main()
