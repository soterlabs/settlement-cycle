"""Reviewed, immutable monthly settlement summaries. No daily computation or network I/O.

Keep previous snapshots when adding a new one: saved annual selections pin report
revisions, including months with no publication. The final snapshot is active.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from functools import lru_cache
from importlib.resources import files

from .financial import metric
from .verification import PRIMES, digest

SNAPSHOTS = ('66854e865be5d6972a975a5069de56a3c83c2704',)
DEMAND_FIELDS = {
    'agent rate': 'demand.agent_rate',
    'distribution rewards': 'demand.distribution_rewards',
    'demand-side revenue': 'demand.total',
    'governance accessibility rewards': 'demand.gar',
    'chronicle points': 'demand.chronicle_points',
}


def table(section):
    result = {}
    rows = [line.split('|')[1:-1] for line in section.splitlines() if line.startswith('|')]
    if not rows or [c.strip() for c in rows[0]] != ['Field', 'USDS']:
        raise ValueError('Expected a USDS headline table')
    for row in rows[2:]:
        if len(row) != 2:
            raise ValueError('Invalid headline row')
        label, amount = (c.strip().replace('**', '') for c in row)
        if label in result or not re.fullmatch(r'-?(?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2}', amount):
            raise ValueError('Duplicate field or invalid monthly amount')
        result[label] = Decimal(amount.replace(',', ''))
    return result


def parse(snapshot, report):
    """Validate exact source bytes and headline scope before accepting any amount."""
    path = re.fullmatch(r'reports/([a-z]+)/([0-9]{4}-[0-9]{2})/summary\.md', report['path'])
    if not path or path[1] not in PRIMES:
        raise ValueError('Unsupported monthly report path')
    prime, month = path[1], path[2]
    start = date.fromisoformat(month + '-01')
    end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    raw = report['text'].encode()
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if (hashlib.sha256(raw).hexdigest() != report['sha256'] or blob != report['git_blob_sha']
            or not report['text'].startswith(f'# {prime.upper()} — {month}\n')
            or f'Period: {start} → {end} ({end.day} days)' not in report['text']):
        raise ValueError('Monthly source identity/hash mismatch')
    text = report['text']
    for heading in ('## Headline\n', '#### Demand-Side revenue\n', '#### Supply-Side revenue\n', '### Sky side\n'):
        if text.count(heading) != 1:
            raise ValueError('Ambiguous monthly headline')
    demand_section = text.split('#### Demand-Side revenue\n')[1].split('#### Supply-Side revenue\n')[0]
    demand = table(demand_section)
    if not {'agent rate', 'distribution rewards', 'demand-side revenue'} <= demand.keys() or demand.keys() - DEMAND_FIELDS.keys():
        raise ValueError('Unsupported demand-side headline')
    if (('governance accessibility rewards' in demand and prime != 'skybase')
            or ('chronicle points' in demand and prime != 'grove')):
        raise ValueError('Unexpected demand entitlement')
    components = [v for k, v in demand.items() if k != 'demand-side revenue']
    delta = demand['demand-side revenue'] - sum(components)
    if abs(delta) > Decimal('0.005') * (len(components) + 1):
        raise ValueError('Demand-side headline does not reconcile within display rounding')
    sky = table(text.split('### Sky side\n')[1].split('\n## ')[0])
    if set(sky) != {'prime cost of funds', 'sky direct exposure', 'supply-side revenue'}:
        raise ValueError('Unsupported Sky headline')
    source = {'kind': 'published_monthly_summary', 'repository': snapshot['repository'],
              'commit': snapshot['commit'], 'path': report['path'], 'git_blob_sha': blob,
              'result_hash': report['sha256'], 'currency': 'USDS', 'display_precision': '0.01',
              'conversion': 'USDS at USD par', 'cutoff': str(end),
              'url': f"https://github.com/{snapshot['repository']}/blob/{snapshot['commit']}/{report['path']}",
              'demand_rounding_delta': str(delta)}
    revision = digest(source)
    source['revision_id'] = revision
    values = {DEMAND_FIELDS[k]: v for k, v in demand.items()}
    values['funding.sky.net'] = sky['prime cost of funds']
    metrics = {}
    for key, value in values.items():
        item = metric(value, basis='published_monthly_settlement_headline_usds_at_par',
                      source_paths=[source['url']])
        item.update(source_ref=revision)
        metrics[key] = item
    return {'prime': prime, 'month': month, 'cutoff': str(end), 'revision_id': revision,
            'source': source, 'metrics': metrics}


@lru_cache(maxsize=1)
def publications():
    retained, active = {}, {}
    for commit in SNAPSHOTS:
        snapshot = json.loads(files('settle.revenue').joinpath('published_monthly', commit + '.json').read_text())
        if (snapshot['repository'] != 'soterlabs/settlement-reports' or snapshot['commit'] != commit
                or snapshot['schema_version'] != 'published-settlement-summaries.v1'):
            raise ValueError('Invalid monthly snapshot')
        heads = {}
        for raw in snapshot['reports']:
            report = parse(snapshot, raw)
            key = (report['prime'], report['month'])
            if key in heads:
                raise ValueError('Duplicate prime/month in snapshot')
            retained[report['revision_id']] = report
            heads[key] = report['revision_id']
        active = heads
    return retained, active


def select(prime, year, as_of, selection=None):
    retained, active = publications()
    chosen = []
    for month in range(1, 13):
        key = f'{year}-{month:02}'
        ref = selection['monthly_revisions'][month - 1] if selection else active.get((prime, key))
        report = retained.get(ref) if ref else None
        if ref and (not report or report['prime'] != prime or report['month'] != key):
            raise ValueError('Retained monthly revision unavailable')
        if report and date.fromisoformat(report['cutoff']) > as_of:
            if selection:
                raise ValueError('Monthly revision exceeds the selected cutoff')
            report = None
        chosen.append(report)
    return chosen
