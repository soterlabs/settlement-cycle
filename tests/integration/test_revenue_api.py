"""Exercise HTTP selection and failure metadata against committed Postgres rows."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from settle.api.app import PostgresReader, create_app
from settle.revenue import store
from settle.revenue.verification import PRIMES
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.integration.test_revenue_store import example


def test_committed_revisions_failed_refresh_and_prime_isolation(database, monkeypatch):  # noqa: F811
    def forbidden(*args, **kwargs):
        raise AssertionError('API must not call providers')
    monkeypatch.setattr('requests.Session.send', forbidden)
    pnl = example()
    cutoff = pnl.as_of.isoformat()
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        for prime in PRIMES:
            store.publish(conn, replace(pnl, prime_id=prime), store.Versions('a', 'cfg', '0'))
        original = store.read(conn, 'obex')['revision_id']
        corrected = store.publish(conn, replace(pnl, revenue=Decimal('2.000000000000000001')),
                                  store.Versions('a', 'cfg', '1'),
                                  input_provenance={'reference_rates': {'coverage_complete': True,
                                                                      'snapshot_id': 'b'*64}})
        store.publish(conn, example(2), store.Versions('a', 'cfg', '0'))
    with ConnectionPool(database, min_size=1, max_size=2) as pool:
        reader = PostgresReader(pool, {}, [], 0)
        with TestClient(create_app(reader=reader)) as client:
            response = client.get('/v1/revenue/obex/latest')
            assert response.status_code == 200
            assert response.json()['data']['revision_id'] == corrected
            assert response.json()['data']['result']['revenue'] == '2.000000000000000001'
            assert response.json()['data']['input_provenance']['reference_rates']['snapshot_id'] == 'b'*64
            assert not response.json()['freshness']['stale']
            assert client.get('/v1/revenue/status').status_code == 200
            old = client.get(f'/v1/revenue/obex/at/{cutoff}?revision={original}')
            assert old.json()['data']['result']['revenue'] == str(pnl.revenue)
            assert client.get(f'/v1/revenue/grove/at/{cutoff}?revision={original}').status_code == 404
            history = client.get('/v1/revenue/obex/history').json()['results']
            assert len(history) == 2
            assert history[0]['revision_id'] == corrected
            revisions = client.get(f'/v1/revenue/obex/revisions/{cutoff}').json()['revisions']
            assert [r['revision_id'] for r in revisions] == [corrected, original]
            with psycopg.connect(database, autocommit=True) as conn:
                conn.execute('''INSERT INTO revenue_attempts
                    (attempt_id, prime, cutoff, versions, status, finished_at, error_type)
                    VALUES (%s, 'obex', %s, '{}', 'failed', NOW(), 'RPCError')''',
                             (uuid4(), pnl.as_of))
            failed_refresh = client.get('/v1/revenue/obex/latest').json()
            assert failed_refresh['data']['revision_id'] == corrected
            assert failed_refresh['latest_attempt']['status'] == 'failed'
            assert not failed_refresh['freshness']['stale']
            status = client.get('/v1/revenue/status')
            assert status.status_code == 503
            assert status.headers['cache-control'] == 'no-store'
            assert status.json()['primes']['obex']['latest_attempt']['error_type'] == 'RPCError'


def test_independent_views_share_persisted_revisions_and_reporting_corrections(database, monkeypatch):  # noqa: F811
    from datetime import timedelta

    from settle.api import revenue
    from settle.compute.venue_views import build_venue_views
    from settle.domain.venue_views import VenueView
    from settle.revenue.verification import digest, validate_window
    from tests.unit.test_venue_views import example as view_example, terms

    def forbidden(*args, **kwargs):
        raise AssertionError('API must not call providers')
    monkeypatch.setattr('requests.Session.send', forbidden)
    prime, pnl, receipts = view_example()
    fixture_today = pnl.as_of + timedelta(days=1)
    monkeypatch.setattr(store, 'validate_window', lambda cutoff: validate_window(cutoff, today=fixture_today))
    monkeypatch.setattr(revenue, 'today', lambda: fixture_today)
    pnl = replace(pnl, venue_views=build_venue_views(prime, pnl, receipts))
    changed = terms()
    changed['tranches'][0]['apr_schedule'][0]['apr'] = '0.2'
    prime = replace(prime, venues=[replace(prime.venues[0], reporting_view=VenueView(
        method='loan_accrual', loan_terms=changed)), prime.venues[1]])
    corrected = replace(pnl, venue_views=build_venue_views(prime, pnl, receipts))
    assert corrected.venue_views['settlement'] == pnl.venue_views['settlement']
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        first = store.publish(conn, pnl, store.Versions('views', 'cfg-a', '0'))
        second = store.publish(conn, corrected, store.Versions('views', 'cfg-b', '0'))
        saved = store.read(conn, 'spark', revision=first)
        assert saved['result_hash'] == digest(saved['result'])
    with ConnectionPool(database, min_size=1, max_size=2) as pool:
        with TestClient(create_app(reader=PostgresReader(pool, {}, [], 0))) as client:
            cutoff = str(pnl.as_of)
            for revision, income in ((first, Decimal(30)), (second, Decimal(60))):
                settle = client.get(f'/v2/settlement/spark/at/{cutoff}?revision={revision}').json()
                report = client.get(f'/v2/reporting/spark/at/{cutoff}?revision={revision}').json()
                assert settle['data']['revision_id'] == report['data']['revision_id'] == revision
                assert settle['data']['result']['totals']['prime_agent_revenue'] == '100'
                assert Decimal(report['data']['result']['venues'][0]['income_usd']) == income
                assert settle['data']['source_result_hash'] == report['data']['source_result_hash']
                raw = client.get(f'/v1/revenue/spark/at/{cutoff}?revision={revision}').json()
                assert raw['data']['result_hash'] == digest(raw['data']['result'])
