#!/usr/bin/env python3
"""Read-only 2026 USDS bridge/CoF audit using canonical gateway events.

Public log transport is independent of runtime provider credentials. Complete
cached raw responses are reused; failed or truncated reads never become zero.
Published monthly workbooks are read only. No report or MSC adjustment is booked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time
from decimal import Decimal, localcontext
from pathlib import Path

import requests
from openpyxl import load_workbook

from settle.compute.sky_revenue import daily_borrow_charge
from settle.domain import Chain, Period
from settle.domain.config import load_prime_by_id
from settle.extract.hypersync import LogRow
from settle.normalize.usds_bridge_transit import ROUTES, collect_transfers, daily_exposure

ROOT = Path(__file__).resolve().parents[1]
RPC = {'ethereum': 'https://eth.drpc.org', 'arbitrum': 'https://arb1.arbitrum.io/rpc',
       'base': 'https://mainnet.base.org', 'optimism': 'https://mainnet.optimism.io',
       'unichain': 'https://mainnet.unichain.org'}
EXPLORERS = {'ethereum': 'https://eth.blockscout.com/api',
             'optimism': 'https://optimism.blockscout.com/api',
             'unichain': 'https://unichain.blockscout.com/api'}
D = Decimal


class PublicLogs:
    def __init__(self, cache):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.headers = {}
        self.witnesses = []

    def rpc(self, chain, method, params):
        for attempt in range(4):
            response = requests.post(RPC[chain], json={'jsonrpc': '2.0', 'id': 1, 'method': method,
                                                      'params': params}, timeout=45)
            response.raise_for_status()
            value = response.json()
            if 'result' in value:
                return value['result']
            if attempt == 3:
                raise ValueError(f'{chain} {method}: {value.get("error")}')
        raise AssertionError('unreachable')

    def block(self, chain, number):
        key = (chain, number)
        if key not in self.headers:
            self.headers[key] = self.rpc(chain, 'eth_getBlockByNumber', [hex(number), False])
        return self.headers[key]

    def pin(self, chain, cutoff):
        latest = int(self.rpc(chain, 'eth_blockNumber', []), 16)
        high_time = int(self.block(chain, latest)['timestamp'], 16)
        target = int(datetime.combine(cutoff, time.max, UTC).timestamp())
        if high_time < target:
            raise ValueError(f'{chain}: requested cutoff is not complete')
        lo, hi = 0, latest
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if int(self.block(chain, mid)['timestamp'], 16) <= target:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def logs(self, chain, selections, start, end, **_):
        gateway = selections[0]['address'][0]
        key = f'{chain}-{gateway}-{end}'
        path = self.cache / (key + '.json')
        if path.exists():
            raw = json.loads(path.read_text())
        else:
            if chain not in EXPLORERS and os.environ.get('ENVIO_API_TOKEN'):
                from settle.extract import hypersync
                result = hypersync.query_logs(chain, [{'address': [gateway]}], start, end,
                        log_fields=[*hypersync._DEFAULT_LOG_FIELDS, 'transaction_hash'])
                if result.archive_height < end:
                    raise ValueError('Indexer has not reached the audit pin')
                raw = [{'address': r.address, 'blockNumber': hex(r.block_number),
                        'logIndex': hex(r.log_index), 'timeStamp': hex(r.block_time),
                        'topics': [r.topic0, r.topic1, r.topic2, r.topic3], 'data': r.data,
                        'transactionHash': r.transaction_hash} for r in result.rows]
            else:
                raw = self.explorer(chain, gateway, start, end) if chain in EXPLORERS else self.rpc_logs(chain, gateway, start, end)
            path.write_text(json.dumps(raw, separators=(',', ':')))
        self.witnesses.append({'chain': chain, 'gateway': gateway, 'from_block': start,
                               'to_block': end, 'raw_logs': len(raw),
                               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        result = []
        seen = {}
        for row in raw:
            if row['address'].lower() != gateway or row.get('removed'):
                raise ValueError('Invalid gateway log scope/reorg')
            topics = [x for x in row['topics'] if x is not None]
            if topics[0] not in selections[0]['topics'][0]:
                continue
            block = int(row['blockNumber'], 16)
            if not start <= block <= end:
                raise ValueError('Gateway log outside requested pins')
            timestamp = row.get('timeStamp') or row.get('blockTimestamp')
            stamp = int(timestamp, 16) if timestamp and int(timestamp, 16) > 0 else int(self.block(chain, block)['timestamp'], 16)
            identity = (row['transactionHash'], int(row['logIndex'], 16))
            if identity in seen:
                if seen[identity] != row:
                    raise ValueError('Conflicting public gateway log identity')
                continue
            seen[identity] = row
            result.append(LogRow(block, identity[1], stamp, gateway,
                                 *(topics + [None] * 4)[:4], row['data'], identity[0]))
        return result

    def explorer(self, chain, gateway, start, end):
        rows = []
        cursor = start
        while cursor <= end:
            response = requests.get(EXPLORERS[chain], params={'module': 'logs', 'action': 'getLogs',
                                     'address': gateway, 'fromBlock': cursor, 'toBlock': end}, timeout=45)
            response.raise_for_status()
            value = response.json()
            if value.get('status') == '0' and value.get('message') == 'No logs found':
                break
            if value.get('status') != '1' or not isinstance(value.get('result'), list):
                raise ValueError(f'{chain}: incomplete explorer read {value.get("message")}')
            page = value['result']
            rows.extend(page)
            if len(page) < 1000:
                break
            last = int(page[-1]['blockNumber'], 16)
            if last <= cursor:
                raise ValueError('Cannot certify gateway log pagination')
            cursor = last  # overlap boundary block: do not lose the 1001st log
        return rows

    def rpc_logs(self, chain, gateway, start, end):
        # Public Arbitrum endpoint permits 10m blocks for this narrow address.
        # Base may impose smaller ranges; split rejected ranges recursively.
        rows = []
        def query(lo, hi):
            try:
                return self.rpc(chain, 'eth_getLogs', [{'address': gateway,
                                 'fromBlock': hex(lo), 'toBlock': hex(hi)}])
            except ValueError:
                if hi - lo < 1000:
                    raise
                mid = (lo + hi) // 2
                return query(lo, mid) + query(mid + 1, hi)
        for lo in range(start, end + 1, 10000000):
            rows.extend(query(lo, min(lo + 9999999, end)))
        return rows


def historical_rows(prime, month):
    files = list((ROOT / 'settlements' / prime / month).glob('*.xlsx'))
    if len(files) != 1:
        return None
    book = load_workbook(files[0], read_only=True, data_only=True)
    try:
        if 'Debt' not in book:
            return None
        header = None
        result = {}
        for row in book['Debt'].values:
            if row[0] == 'Date':
                header = list(row)
                continue
            if header is None or not isinstance(row[0], str) or not row[0].startswith('2026-'):
                continue
            result[row[0]] = dict(zip(header, row, strict=True))
            # Excel treats a header beginning '=' as a formula without cache.
            result[row[0]]['utilized'] = row[header.index('SSR APY') - 1]
            result[row[0]]['bridge_usds'] = (row[header.index('- In-transit USDS')]
                                             if '- In-transit USDS' in header else '0')
        return result
    finally:
        book.close()


def cost_delta(row, amount, cap):
    """Fit frozen daily rate convention; never restate it using today's rate."""
    if D(str(row.get('bridge_usds', '0'))) != 0:
        raise ValueError('Baseline already contains a bridge deduction; compare incrementally')
    utilized, actual = D(str(row['utilized'])), D(str(row['daily Sky charge']))
    base = D(str(row.get('base APR', row.get('base APY'))))
    sub_raw = row.get('sub APR', row.get('sub APY'))
    sub = D(str(sub_raw)) if sub_raw is not None else base
    cap = cap if sub_raw is not None else D(0)
    with localcontext() as ctx:
        ctx.prec = 60
        def factors(rate):
            return [rate / 365, (1 + rate) ** (D(1) / 365) - 1,
                    ((1 + rate) ** (D(1) / 12) - 1) * 12 / 365]
        candidates = []
        for bf in factors(base):
            for sf in factors(sub):
                before = daily_borrow_charge(utilized, base_factor=bf, subsidised_factor=sf, subsidy_cap=cap)
                if abs(before - actual) <= D('.00001'):
                    after = daily_borrow_charge(utilized - amount, base_factor=bf, subsidised_factor=sf, subsidy_cap=cap)
                    candidates.append(before - after)
        if not candidates or max(candidates) - min(candidates) > D('.00001'):
            return None
        return candidates[0]


def bridge_scope(prime, period):
    active = prime.chains_at(period.end)
    supported = sorted(c.value for c in ROUTES if c in prime.alm and c in active
                       and Chain.ETHEREUM in prime.alm)
    return {'result_scope': 'supported_native_usds_routes_only',
            'supported_ethereum_routes': supported,
            'unscanned_alm_chains': sorted(c.value for c in prime.alm if c in active
                                         and c != Chain.ETHEREUM and c.value not in supported)}


def audit(cutoff, cache):
    transport = PublicLogs(cache)
    with ThreadPoolExecutor(max_workers=3) as executor:
        pins = dict(executor.map(lambda c: (Chain(c), transport.pin(c, cutoff)), RPC))
    print('Pinned', {c.value: n for c, n in pins.items()}, flush=True)
    period = Period(date(2026, 1, 1), cutoff, pins)
    all_transfers, by_prime, exceptions = [], {}, []
    for name in ['spark', 'grove', 'obex', 'osero', 'keel', 'skybase']:
        prime = load_prime_by_id(name, config_dir=ROOT / 'config')
        print('Scanning', name, flush=True)
        transfers = collect_transfers(prime, period, fetch=transport.logs)
        selected = [t for t in transfers if datetime.fromtimestamp(t['sent_at'], UTC).date() <= cutoff
                    and (t['received_at'] is None or datetime.fromtimestamp(t['received_at'], UTC).date() >= period.start)]
        daily = daily_exposure(period, selected)
        charges = []
        measured = D(0)
        books = {}
        retained = {}
        supplemental = cache / f'{name}-october-retained-daily.json'
        if supplemental.exists():
            retained_source = json.loads(supplemental.read_text())
            if retained_source['prime'] != name or retained_source['month'] != '2026-10':
                raise ValueError('Wrong retained source identity')
            retained = {row['date']: row for row in retained_source['daily']}
        for row in daily.to_dict('records'):
            day, amount = row['block_date'], row['cum_balance']
            if amount == 0:
                continue
            month = day.strftime('%Y-%m')
            if month not in books:
                books[month] = historical_rows(name, month)
            original = (books[month] or {}).get(str(day))
            if original is None and str(day) in retained:
                r = retained[str(day)]
                original = {'utilized': r['utilized'], 'daily Sky charge': r['daily_sky_rev'],
                            'base APR': r['base_apr'], 'sub APR': r.get('sub_apr'),
                            'bridge_usds': r.get('bridge_usds', '0')}
            delta = cost_delta(original, amount, prime.subsidy.cap_usd) if original else None
            if delta is not None:
                measured += delta
            charges.append({'date': str(day), 'in_transit_usds': str(amount),
                            'original_charge': str(original['daily Sky charge']) if original else None,
                            'measured_excess_charge': str(delta) if delta is not None else None,
                            'status': ('frozen_charge_reproduced' if delta is not None else
                                       'rate_convention_unresolved' if original else 'no_saved_daily_charge')})
        baselines = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for month in books for path in (ROOT / 'settlements' / name / month).glob('*.xlsx')}
        by_prime[name] = {**bridge_scope(prime, period), 'baseline_workbook_sha256': baselines,
                          'retained_source': ({key: retained_source[key] for key in ['revision_id','result_hash','code_version','configuration_version']}
                                              if retained else None),
                          'transfers': len(selected), 'transit_closing_days': len(charges),
                          'measured_excess_charge': str(measured), 'daily': charges}
        for t in selected:
            all_transfers.append({'prime': name, **t})
        print(name, len(selected), 'transfers;', len(charges), 'closing days; measured', measured, flush=True)
    return {'scope': f'Canonical native USDS bridges, configured prime ALMs, 2026-01-01 through {cutoff}',
            'settlement_execution_verified': False,
            'method': 'Authenticated gateway payloads; frozen workbook charges at their original rate convention. No MSC adjustment booked.',
            'code_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                           ['src/settle/normalize/usds_bridge_transit.py', 'scripts/audit_usds_bridge_transit_2026.py']},
            'prime_config_sha256': {name: hashlib.sha256((ROOT / 'config' / (name + '.yaml')).read_bytes()).hexdigest()
                                  for name in by_prime},
            'pins': {c.value: n for c, n in pins.items()}, 'coverage': transport.witnesses,
            'transfers': all_transfers, 'primes': by_prime, 'exceptions': exceptions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through', type=date.fromisoformat, default=date(2026, 11, 8))
    parser.add_argument('--cache', type=Path, default=Path('/tmp/usds-bridge-2026-cache'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--env-file', type=Path, help='Read only ENVIO_API_TOKEN from this local file')
    args = parser.parse_args()
    if args.env_file:
        from dotenv import dotenv_values
        value = dotenv_values(args.env_file).get('ENVIO_API_TOKEN')
        if not value:
            raise ValueError('The supplied file has no indexer credential')
        os.environ['ENVIO_API_TOKEN'] = value
    result = audit(args.through, args.cache)
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text)


if __name__ == '__main__':
    main()
