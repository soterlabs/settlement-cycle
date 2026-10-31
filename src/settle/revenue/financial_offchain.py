"""Immutable, counterparty-supplied monthly Grove accrual schedules.

No daily reconstruction, live forecasting or MSC settlement changes. Retain old
snapshots when importing new schedules so saved report selections still replay.
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
from datetime import date
from decimal import Decimal, localcontext
from functools import lru_cache
from importlib.resources import files

from .financial import metric, number
from .verification import digest

SNAPSHOTS = ('a3d115d440858134d2c63b608a135c1a6528e230611a4266054f8ef5bdc9aa0f',)
SPECS = {
    'gaclo': ('E21', 'GACLO1', 'GACLO', 'data/gaclo-monthly.csv'),
    'warehouse': ('E42', 'GLXYWH', 'Galaxy Warehouse', 'data/warehouse-monthly.csv'),
}
FIELDS = {
    'opening_principal': 'openingPrincipal', 'principal_additions': 'principalAdditions',
    'principal_returns': 'principalReturns', 'closing_principal': 'closingPrincipal',
    'earned_income': 'earnedIncome', 'cash_received': 'incomeCash',
    'opening_receivable': 'openingReceivable', 'closing_receivable': 'closingReceivable',
    'fees': 'fees', 'adjustments': 'adjustments', 'valuation_changes': 'valuationChanges',
}


def taxonomy(prime):
    return [{'id': f'investment.offchain.{key}', 'label': spec[2],
             'family': 'Offchain lending', 'aggregation': 'leaf'}
            for key, spec in SPECS.items()] if prime == 'grove' else []


def parse(snapshot):
    if (snapshot['schema_version'] != 'grove-monthly-accruals.v1'
            or len(snapshot['sources']) != 2):
        raise ValueError('Invalid offchain snapshot')
    grouped, seen = {}, set()
    for source in snapshot['sources']:
        if hashlib.sha256(source['text'].encode()).hexdigest() != source['sha256']:
            raise ValueError('Offchain source hash mismatch')
        for raw in csv.DictReader(io.StringIO(source['text'])):
            component = raw['component']
            venue, position, label, path = SPECS[component]
            month = raw['month']
            start = date.fromisoformat(month + '-01')
            end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
            # The supplied October Warehouse schedule explicitly ends on Oct 25.
            if component == 'warehouse' and month == '2026-10':
                end = date(2026, 10, 25)
            if (source['path'] != path or raw['currency'] != 'USD' or raw['position'] != position
                    or raw['coverage'] not in ('supported', 'estimate') or (component, month) in seen):
                raise ValueError('Invalid offchain position/period identity')
            seen.add((component, month))
            values = {key: number(raw[field]) if raw[field] else None for key, field in FIELDS.items()}
            if any(values[key] is None for key in FIELDS if key != 'fees'):
                raise ValueError('Incomplete offchain schedule')
            with localcontext() as ctx:
                ctx.prec = 60
                principal_delta = (values['opening_principal'] + values['principal_additions']
                                   - values['principal_returns'] - values['closing_principal'])
                receivable_delta = (values['opening_receivable'] + values['earned_income']
                                    - values['cash_received'] + values['adjustments'] - values['closing_receivable'])
            if (abs(principal_delta) > Decimal('0.01') or abs(receivable_delta) > Decimal('0.01')
                    or values['opening_principal'] < 0 or values['closing_principal'] < 0
                    or values['opening_receivable'] < 0 or values['closing_receivable'] < 0
                    or values['valuation_changes'] != 0):
                raise ValueError('Offchain balances do not reconcile')
            grouped.setdefault(month, []).append({
                'component': component, 'source_venue_id': venue, 'position_id': position, 'label': label,
                'period_start': str(start), 'period_end': str(end), 'quality': raw['coverage'],
                'source_path': path, 'source_sha256': source['sha256'],
                'raw': raw, 'amounts': values,
            })
    reports = []
    for month, rows in sorted(grouped.items()):
        source = {'kind': 'counterparty_monthly_accrual', 'cutoff': max(row['period_end'] for row in rows),
                  'currency': 'USD', 'archive_sha256': snapshot['archive_sha256'],
                  'received_at': snapshot['received_at'], 'result_hash': digest([row['raw'] for row in rows]),
                  'files': [{'path': row['source_path'], 'sha256': row['source_sha256']} for row in rows],
                  'limitations': ['Separate fees not supplied; income is not certified net of fees',
                      'GACLO opening receivable is source-derived and proposed for review',
                      'Warehouse metadata awaits servicing confirmation; original July invoice absent']}
        ref = digest(source)
        source['revision_id'] = ref
        positions, metrics = [], {}
        for row in rows:
            amounts = {}
            for key, value in row['amounts'].items():
                cell = metric(value, reason='fees_not_supplied', basis='counterparty_monthly_schedule',
                              source_paths=[row['source_path'] + '#' + month + '.' + FIELDS[key]])
                quality = row['quality']
                if row['component'] == 'gaclo' and key in ('opening_receivable', 'closing_receivable'):
                    quality = 'estimate'  # Opening carry is proposed, even in reported-income months.
                # Cash is supplied history, including months whose income is estimated.
                if key == 'cash_received':
                    quality = 'supported'
                cell.update(source_ref=ref, quality=quality, period_start=row['period_start'], period_end=row['period_end'])
                if value is not None:
                    cell['recognition'] = ('accrual' if key == 'earned_income' else 'receipt' if key == 'cash_received'
                                           else 'balance' if 'principal' in key or 'receivable' in key else 'adjustment')
                period_end = date.fromisoformat(row['period_end'])
                partial_month = period_end.day < calendar.monthrange(period_end.year, period_end.month)[1]
                if value is not None and partial_month and key in ('earned_income', 'cash_received'):
                    cell.update(value=None, known_value=str(value), status='partial', reason='partial_month_schedule')
                amounts[key] = cell
            positions.append({k: row[k] for k in ('component', 'source_venue_id', 'position_id', 'label', 'period_start', 'period_end')} | {'metrics': amounts})
            metrics[f"investment.offchain.{row['component']}"] = amounts['earned_income']
        reports.append({'prime': 'grove', 'month': month, 'cutoff': source['cutoff'], 'revision_id': ref,
                        'source': source, 'metrics': metrics, 'positions': positions})
    return reports


@lru_cache(maxsize=1)
def publications():
    retained, active = {}, {}
    for name in SNAPSHOTS:
        raw = files('settle.revenue').joinpath('published_offchain', name + '.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != name:
            raise ValueError('Offchain snapshot hash mismatch')
        heads = {}
        for report in parse(json.loads(raw)):
            retained[report['revision_id']] = report
            heads[(report['prime'], report['month'])] = report['revision_id']
        active = heads
    return retained, active


def select(prime, year, as_of, selection=None):
    retained, active = publications()
    chosen = []
    for month in range(1, 13):
        key = f'{year}-{month:02}'
        ref = selection['offchain_revisions'][month - 1] if selection else active.get((prime, key))
        report = retained.get(ref) if ref else None
        if ref and (not report or report['prime'] != prime or report['month'] != key):
            raise ValueError('Retained offchain revision unavailable')
        if report and date.fromisoformat(report['cutoff']) > as_of:
            if selection:
                raise ValueError('Offchain revision exceeds selected cutoff')
            report = None
        chosen.append(report)
    return chosen


def extend(statement, chosen):
    statement['offchain_positions'] = []
    if statement['prime'] != 'grove':
        return
    for component, spec in SPECS.items():
        periods = {report['month']: next((p for p in report['positions'] if p['component'] == component), None)
                   for report in chosen if report}
        statement['offchain_positions'].append({'source_venue_id': spec[0], 'label': spec[2],
            'values': {month: row for month, row in periods.items() if row}})
    statement['source_manifest'].update({report['revision_id']: report['source'] for report in chosen if report})
