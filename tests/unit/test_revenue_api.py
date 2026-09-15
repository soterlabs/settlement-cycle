from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from settle.api import revenue
from settle.api.app import create_app
from settle.revenue.verification import PRIMES


class Reader:
    def __init__(self):
        self.row = {'prime': 'obex', 'cutoff': '2026-09-14', 'revision_id': 'a'*64,
                    'computed_at': '2026-09-15T03:30:00+00:00', 'provisional': True,
                    'excluded_inputs': ['monthly_distribution_rewards'],
                    'result': {'monthly_pnl': '1.000000000000000001'}}
        self.attempt = None
        self.calls = []

    def revenue_bundle(self, prime, **kwargs):
        self.calls.append((prime, kwargs))
        return self.row, self.attempt

    def revenue_history(self, *args, **kwargs): return [self.row] if self.row else []
    def revenue_revisions(self, *args, **kwargs): return [{'revision_id': 'a'*64}]
    def revenue_status(self): return {p: (self.row, self.attempt) for p in PRIMES}


def client(reader, monkeypatch):
    monkeypatch.setattr(revenue, 'today', lambda: date(2026, 9, 15))
    return TestClient(create_app(reader=reader))


def test_daily_latest_exact_values_provenance_and_no_provider_calls(monkeypatch):
    r = Reader()
    with patch('requests.Session.send', side_effect=AssertionError('API must not call providers')):
        response = client(r, monkeypatch).get('/v1/revenue/obex/latest')
    assert response.status_code == 200
    body = response.json()
    assert body['cadence'] == 'daily'
    assert body['estimate_basis'] == 'month_to_date'
    assert body['data']['result']['monthly_pnl'] == '1.000000000000000001'
    assert not body['freshness']['stale']
    assert body['data']['provisional']
    assert 'etag' in response.headers


def test_failed_run_retains_previous_result_but_is_stale_and_alerts(monkeypatch):
    r = Reader()
    r.row['cutoff'] = '2026-09-13'
    r.attempt = {'status': 'failed', 'cutoff': '2026-09-14', 'error_type': 'RPCError'}
    c = client(r, monkeypatch)
    result = c.get('/v1/revenue/obex/latest').json()
    assert result['freshness']['stale']
    assert result['latest_attempt']['status'] == 'failed'
    assert c.get('/v1/revenue/status').status_code == 503
    r.row = None
    assert c.get('/v1/revenue/obex/latest').status_code == 404


def test_query_validation_prevents_invalid_selection(monkeypatch):
    r = Reader()
    c = client(r, monkeypatch)
    assert c.get('/v1/revenue/unknown/latest').status_code == 404
    assert c.get('/v1/revenue/obex/at/2026-09-15').status_code == 422
    assert c.get('/v1/revenue/obex/at/2026-09-14?revision=bad').status_code == 422
    assert c.get('/v1/revenue/obex/history?start=2026-01-01&end=2026-09-14').status_code == 422
    assert c.get('/v1/revenue/obex/history?limit=91').status_code == 422
    assert not r.calls
    assert c.get('/v1/revenue/obex/at/2026-09-14?revision='+'b'*64).status_code == 200
    assert r.calls[-1][1] == {'cutoff': date(2026, 9, 14), 'revision': 'b'*64}


def test_store_failure_is_503_without_secrets(monkeypatch):
    r = Reader()
    def failed(*args, **kwargs): raise RuntimeError('postgres://private-secret')
    r.revenue_bundle = failed
    response = client(r, monkeypatch).get('/v1/revenue/obex/latest')
    assert response.status_code == 503
    assert 'private-secret' not in response.text
