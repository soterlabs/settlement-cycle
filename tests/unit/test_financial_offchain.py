import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from settle.revenue.financial import (
    ALLOCATION_REWARDS_POLICY,
    OFFCHAIN_POLICY,
    decode_selection,
    statements,
)
from settle.revenue.financial_offchain import SNAPSHOTS, parse, publications
from settle.revenue.verification import digest

ROOT = Path(__file__).parents[2]


def row(report, key):
    return next(line for line in report['lines'] if line['id'] == key)


def preview(**kwargs):
    saved = json.loads((ROOT / 'tests/fixtures/grove_rwa_financial_2026_10.json').read_text())
    return statements('grove', 2026, date(2026, 10, 28), [saved], offchain=True, **kwargs)


def test_all_supplied_history_with_estimates_and_no_invented_months():
    report = preview()
    assert report['policy_version'] == OFFCHAIN_POLICY
    gaclo = row(report, 'investment.offchain.gaclo')
    wh = row(report, 'investment.offchain.warehouse')
    for month in range(1, 8):
        assert gaclo['values'][f'2026-{month:02}']['quality'] == 'supported'
    assert gaclo['values']['2026-01']['value'] == '402939.81094444456'
    for month in (8, 9):
        assert gaclo['values'][f'2026-{month:02}']['quality'] == 'estimate'
    assert gaclo['values']['2026-10']['value'] is None
    assert wh['values']['2026-06']['value'] is None
    assert wh['values']['2026-09']['value'] == '1894876.53'
    assert wh['values']['2026-10']['period_end'] == '2026-10-25'
    assert wh['values']['2026-10']['quality'] == 'estimate'
    assert wh['values']['2026-10']['status'] == 'partial'
    assert wh['values']['2026-10']['value'] is None
    assert wh['values']['2026-10']['known_value'] == '1716294.145833333'
    assert wh['values']['2026-11']['status'] == 'future'
    assert wh['total']['value'] is None  # pre-July has not been certified zero.
    assert wh['total']['quality'] == 'estimate'
    assert row(report, 'statement.net_income')['values']['2026-10']['quality'] == 'estimate'


def test_income_cash_and_balance_reconcile_without_double_counting():
    report = preview()
    old = statements('grove', 2026, date(2026, 10, 28),
        [json.loads((ROOT / 'tests/fixtures/grove_rwa_financial_2026_10.json').read_text())], allocation_rewards=True)
    for position in report['offchain_positions']:
        for period in position['values'].values():
            values = period['metrics']
            def n(key, values=values):
                return Decimal(values[key]['value'] or values[key]['known_value'])
            assert abs(n('opening_receivable') + n('earned_income') - n('cash_received') - n('closing_receivable')) < Decimal('0.01')
            assert values['fees']['value'] is None
            assert values['cash_received']['recognition'] == 'receipt'
            assert values['cash_received']['quality'] == 'supported'
    gaclo = report['offchain_positions'][0]['values']['2026-01']['metrics']
    assert gaclo['earned_income']['quality'] == 'supported'
    assert gaclo['closing_receivable']['quality'] == 'estimate'
    for month in ('2026-01', '2026-07', '2026-09', '2026-10'):
        added = sum((Decimal(line['values'][month]['value'] or line['values'][month]['known_value']) for line in report['lines']
                     if line['id'].startswith('investment.offchain.') and (line['values'][month]['value'] or line['values'][month]['known_value']) is not None), Decimal(0))
        for key in ('statement.interest', 'statement.net_revenue', 'statement.net_income'):
            def amount(r, key=key, month=month):
                m = row(r, key)['values'][month]
                return Decimal(m['value'] or m['known_value'] or '0')
            assert amount(report) - amount(old) == added


def test_saved_selections_pin_the_offchain_snapshot_and_old_policies():
    report = preview()
    selection = decode_selection(report['selection'], 'grove', 2026)
    replay = preview(selection=selection)
    assert replay == report
    # A saved explicit absence must not start following active publications.
    selection['offchain_revisions'][0] = None
    absent = preview(selection=selection)
    assert row(absent, 'investment.offchain.gaclo')['values']['2026-01']['value'] is None
    selection['offchain_revisions'][0] = '0' * 64
    with pytest.raises(ValueError, match='Retained offchain'):
        preview(selection=selection)
    old = statements('grove', 2026, date(2026, 10, 28), [], allocation_rewards=True)
    old_selection = decode_selection(old['selection'], 'grove', 2026)
    assert old['policy_version'] == ALLOCATION_REWARDS_POLICY
    assert preview(selection=old_selection)['policy_version'] == ALLOCATION_REWARDS_POLICY
    assert 'offchain_positions' not in preview(selection=old_selection)


def test_cutoffs_never_claim_october_25_data_before_that_date_or_extrapolate():
    before = statements('grove', 2026, date(2026, 10, 24), [], offchain=True)
    assert row(before, 'investment.offchain.warehouse')['values']['2026-10']['value'] is None
    after = statements('grove', 2026, date(2026, 11, 2), [], offchain=True)
    assert row(after, 'investment.offchain.warehouse')['values']['2026-10']['period_end'] == '2026-10-25'
    assert row(after, 'investment.offchain.warehouse')['values']['2026-11']['value'] is None
    for prime in ('spark', 'obex', 'keel', 'skybase', 'osero'):
        report = statements(prime, 2026, date(2026, 10, 28), [], offchain=True)
        assert report['offchain_positions'] == []
        assert not any(line['id'].startswith('investment.offchain.') for line in report['lines'])


@pytest.mark.parametrize('column,replacement', [('currency', 'EUR'), ('position', 'other'),
    ('earnedIncome', 'NaN'), ('closingPrincipal', '1'), ('closingReceivable', '1'), ('coverage', 'final')])
def test_malformed_or_unreconciled_sources_fail_closed(column, replacement):
    import csv
    import hashlib
    import io
    snapshot = json.loads((ROOT / 'src/settle/revenue/published_offchain' / (SNAPSHOTS[0] + '.json')).read_text())
    source = snapshot['sources'][0]
    reader = csv.DictReader(io.StringIO(source['text']))
    rows = list(reader)
    rows[0][column] = replacement
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    source['text'] = out.getvalue()
    source['sha256'] = hashlib.sha256(source['text'].encode()).hexdigest()
    with pytest.raises((ValueError, KeyError)):
        parse(snapshot)


def test_source_hashes_and_report_hash_are_verified():
    retained, _ = publications()
    assert len(retained) == 10
    for publication in retained.values():
        source = deepcopy(publication['source'])
        revision = source.pop('revision_id')
        assert digest(source) == revision
    snapshot = json.loads((ROOT / 'src/settle/revenue/published_offchain' / (SNAPSHOTS[0] + '.json')).read_text())
    snapshot['sources'][0]['text'] += '\n'
    with pytest.raises(ValueError, match='hash'):
        parse(snapshot)


def test_snapshot_cutoffs_are_explicit_and_cannot_cross_months():
    snapshot = json.loads((ROOT / 'src/settle/revenue/published_offchain' / (SNAPSHOTS[0] + '.json')).read_text())
    warehouse = snapshot['sources'][1]
    assert warehouse['period_ends'] == {'2026-10': '2026-10-25'}
    warehouse['period_ends']['2026-10'] = '2026-11-01'
    with pytest.raises(ValueError, match='reporting month'):
        parse(snapshot)
