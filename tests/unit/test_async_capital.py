"""Canonical transfers/queue receipts, not a hand-entered redemption calendar."""
import calendar
import gzip
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.domain.config import load_prime_by_id
from settle.domain.period import Period
from settle.extract import hypersync_store, rpc
from settle.extract.hypersync import LogRow
from settle.normalize import async_capital as ac

ROOT = Path(__file__).resolve().parents[2] / 'reconciliation/async-allocations-2026-01_to_2026-09'


def install(monkeypatch):
    h = json.load(gzip.open(ROOT / 'history.json.gz'))
    q = json.load(gzip.open(ROOT / 'state-quotes.json.gz'))
    rows = [LogRow(**r) for group in h['queries'].values() for r in group['rows']]
    monkeypatch.setenv('SETTLE_NO_CACHE', '1')
    def fetch(chain, selections, start, pin, **kw):
        def match(r, s):
            return (r.address in s['address'] and all(not t or getattr(r, 'topic'+str(i)) in t
                    for i, t in enumerate(s.get('topics', []))))
        return [r for r in rows if start <= r.block_number <= pin and any(match(r, s) for s in selections)]
    def post(url, method, params):
        key = json.dumps([method, params], sort_keys=True, separators=(',', ':'))
        if key not in q['rpc']:
            raise AssertionError('Unrecorded canonical RPC input')
        return q['values'][q['rpc'][key]]
    monkeypatch.setattr(rpc, '_post', post)
    monkeypatch.setattr(rpc, 'rpc_url', lambda chain: 'recorded')
    monkeypatch.setattr(hypersync_store, 'fetch_logs', fetch)
    monkeypatch.setattr(ac, 'receipt', lambda chain, tx: h['maple_return_receipts'][tx])
    class Resolver:
        def block_at_or_before(self, chain, dt):
            return q['boundaries'][dt.date().isoformat()]
    return q, Resolver()


@pytest.mark.parametrize('name,vid,month,expected', [
    ('spark','S14',1,'9948.92585762339100'),
    ('spark','S15',4,'4161.79148017835100'),
    ('obex','V1',7,'6187.96807716944700'),
])
def test_canonical_owner_scoped_queues_refunds_and_payments(monkeypatch, name, vid, month, expected):
    q, resolver = install(monkeypatch)
    prime = load_prime_by_id(name)
    venue = next(v for v in prime.venues if v.id == vid)
    start, end = date(2026,month,1), date(2026,month,calendar.monthrange(2026,month)[1])
    period = Period(start,end,{venue.chain:q['boundaries'][str(end)]})
    opening = q['boundaries'][str(date.fromordinal(start.toordinal()-1))]
    prices = {b:D(q['prices'][day+'/'+venue.token.address.hex]) for day,b in q['boundaries'].items()}
    events = ac.capital_events(prime,venue,period,opening_block=opening,block_resolver=resolver,
                               nav_at_block=lambda b:prices[b])
    assert sum((r['capital_adjustment_usd'] for r in events),D(0)) == D(expected)
    assert all(r['block'] <= period.pin_blocks[venue.chain] for r in events)


def test_ethena_complete_earlier_history_closes_to_zero_in_2026(monkeypatch):
    q, resolver = install(monkeypatch)
    prime=load_prime_by_id('spark')
    venue=next(v for v in prime.venues if v.id=='S16')
    period=Period(date(2026,1,1),date(2026,9,30),{venue.chain:q['boundaries']['2026-09-30']})
    assert ac.capital_events(prime,venue,period,opening_block=q['boundaries']['2025-12-31'],
                             block_resolver=resolver,nav_at_block=lambda b:D(1)) == []
