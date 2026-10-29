"""Monthly published figures must work without daily history or provider access."""
import hashlib
import json
from copy import deepcopy
from datetime import date
from importlib.resources import files

import pytest
from fastapi.testclient import TestClient

from settle.api.app import create_app
from settle.revenue import monthly_reports
from settle.revenue.financial import (
    POLICY_VERSION,
    STATEMENT_POLICY,
    decode_selection,
    selection_token,
    statements,
)
from tests.unit.test_financial_reporting import publication


def line(report, key):
    return next(item for item in report['lines'] if item['id'] == key)


def test_all_pinned_summaries_validate_without_daily_data():
    retained, active = monthly_reports.publications()
    assert len(retained) == len(active) == 48
    for prime in ('spark', 'grove', 'obex', 'keel', 'skybase', 'osero'):
        report = statements(prime, 2026, date(2026, 10, 28), [], monthly=True)
        assert report['policy_version'] == STATEMENT_POLICY
        for month in range(1, 10):
            cell = line(report, 'demand.distribution_rewards')['values'][f'2026-{month:02}']
            assert (cell['value'] is not None) == (prime != 'osero' or month >= 7)
        assert line(report, 'demand.total')['values']['2026-10']['value'] is None
        assert line(report, 'demand.total')['total']['value'] is None
        assert line(report, 'demand.total')['total']['known_value'] is not None
        assert any(item['id'] == 'demand.gar' for item in report['lines']) == (prime == 'skybase')


def test_historical_values_are_published_headlines_not_reconstructed_daily():
    report = statements('spark', 2026, date(2026, 10, 28), [], monthly=True)
    for key, amount in [('demand.agent_rate', '106392.55'),
                        ('demand.distribution_rewards', '1140484.75'),
                        ('demand.total', '1246877.30'), ('funding.sky.net', '7842258.86')]:
        cell = line(report, key)['values']['2026-01']
        assert cell['value'] == amount
        source = report['source_manifest'][cell['source_ref']]
        assert source['currency'] == 'USDS' and source['display_precision'] == '0.01'
        assert source['cutoff'] == '2026-01-31'
    assert line(report, 'funding.sky.gross')['values']['2026-01']['value'] is None


def test_monthly_overlay_preserves_daily_allocations_and_mtd_without_double_counting():
    rows = [publication('2026-09-29'), publication('2026-09-30'), publication('2026-10-25'), publication()]
    original = deepcopy(rows)
    report = statements('obex', 2026, date(2026, 10, 28), rows, monthly=True)
    native = line(report, 'investment.maple_syrup_usdc')
    assert native['values']['2026-09']['value'] == '100'
    assert native['values']['2026-10']['value'] == '100'
    assert native['values']['2026-01']['value'] is None
    assert native['total']['known_value'] == '200'
    assert line(report, 'demand.agent_rate')['values']['2026-10']['value'] == '12.000000000000000001'
    assert report['periods'][8]['monthly_revision']
    assert report['periods'][8]['financial_revision']
    assert native['values']['2026-09']['source_ref'] == rows[1]['revision_id']
    assert line(report, 'demand.agent_rate')['values']['2026-09']['source_ref'] == report['periods'][8]['monthly_revision']
    assert report['periods'][9]['stale'] is True
    assert rows == original


def test_monthly_sources_never_leak_beyond_selected_cutoff():
    report = statements('spark', 2026, date(2026, 8, 15), [], exact=True, monthly=True)
    assert line(report, 'demand.total')['values']['2026-07']['value'] is not None
    assert line(report, 'demand.total')['values']['2026-08']['value'] is None
    assert report['periods'][7]['monthly_revision'] is None
    selection = decode_selection(report['selection'], 'spark', 2026)
    selection['monthly_revisions'][7] = monthly_reports.publications()[1][('spark', '2026-08')]
    with pytest.raises(ValueError, match='exceeds'):
        statements('spark', 2026, date(2026, 8, 15), [], selection=selection)


def test_retained_dual_source_selections_freeze_corrections_and_missing_months(monkeypatch):
    rows = [publication()]
    report = statements('obex', 2026, date(2026, 10, 28), rows, monthly=True)
    selection = decode_selection(report['selection'], 'obex', 2026)
    retained, _ = monthly_reports.publications()
    monkeypatch.setattr(monthly_reports, 'publications', lambda: (retained, {}))
    assert statements('obex', 2026, date(2026, 10, 28), [*rows, publication('2026-10-28')], selection=selection) == report
    selection['monthly_revisions'][0] = 'a' * 64
    with pytest.raises(ValueError, match='unavailable'):
        statements('obex', 2026, date(2026, 10, 28), rows, selection=selection)


def test_monthly_composition_does_not_follow_future_daily_policy_default(monkeypatch):
    from settle.revenue import financial
    expected = statements('spark', 2026, date(2026, 10, 28), [], monthly=True)
    monkeypatch.setattr(financial, 'POLICY_VERSION', 'future-policy')
    assert statements('spark', 2026, date(2026, 10, 28), [], monthly=True) == expected


@pytest.mark.parametrize('change', [[], {'monthly_revisions': []}, {'monthly_revisions': [True] * 12}])
def test_malformed_monthly_selectors_rejected(change):
    report = statements('spark', 2026, date(2026, 8, 31), [], monthly=True)
    selection = decode_selection(report['selection'], 'spark', 2026)
    selection = {**selection, **change} if isinstance(change, dict) else change
    with pytest.raises(ValueError):
        decode_selection(selection_token(selection), 'spark', 2026)


@pytest.mark.parametrize('before,after', [
    ('1,140,484.75', '1,140,485.75'),
    ('| agent rate |', '| unsupported reward |'),
    ('| agent rate | 106,392.55 |', '| agent rate | NaN |'),
    ('| agent rate | 106,392.55 |', '| agent rate | 106,392.55 |\n| agent rate | 106,392.55 |'),
    ('| Field | USDS |', '| Field | USD |'),
    ('|---|---:|', '| ignored field | 100.00 |'),
    ('| prime cost of funds | 7,842,258.86 |', '| prime cost of funds | 99.00 |'),
    ('2026-01-31 (31 days)', '2026-01-30 (30 days)'),
])
def test_invalid_summaries_fail_closed(before, after):
    snapshot = json.loads(files('settle.revenue').joinpath('published_monthly', monthly_reports.SNAPSHOTS[0] + '.json').read_text())
    raw = deepcopy(next(r for r in snapshot['reports'] if r['path'] == 'reports/spark/2026-01/summary.md'))
    assert before in raw['text']
    raw['text'] = raw['text'].replace(before, after)
    payload = raw['text'].encode()
    raw['sha256'] = hashlib.sha256(payload).hexdigest()
    raw['git_blob_sha'] = hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
    with pytest.raises(ValueError):
        monthly_reports.parse(snapshot, raw)


def test_http_monthly_history_works_without_saved_daily_rows_and_pins_policy(monkeypatch):
    from settle.api import revenue
    monkeypatch.setattr(revenue, 'today', lambda: date(2026, 10, 29))
    def forbidden(*args, **kwargs):
        raise AssertionError('No network/provider calls')
    monkeypatch.setattr('requests.Session.send', forbidden)
    class Reader:
        def financial_year(self, prime, year, revisions=None, policy_version=POLICY_VERSION):
            assert policy_version == POLICY_VERSION
            return []
    client = TestClient(create_app(reader=Reader()))
    params = {'prime': 'spark', 'year': 2026}
    response = client.get('/v3/reporting/statements', params=params)
    assert response.status_code == 200
    report = response.json()
    assert line(report, 'demand.distribution_rewards')['values']['2026-01']['value'] == '1140484.75'
    assert client.get('/v3/reporting/statements', params={**params, 'revision': report['selection']}).json() == report
    assert client.get('/v3/reporting/statements', params=params, headers={'If-None-Match': response.headers['etag']}).status_code == 304
