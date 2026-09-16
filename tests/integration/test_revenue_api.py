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
                                  store.Versions('a', 'cfg', '1'))
        store.publish(conn, example(2), store.Versions('a', 'cfg', '0'))
    with ConnectionPool(database, min_size=1, max_size=2) as pool:
        reader = PostgresReader(pool, {}, [], 0)
        with TestClient(create_app(reader=reader)) as client:
            response = client.get('/v1/revenue/obex/latest')
            assert response.status_code == 200
            assert response.json()['data']['revision_id'] == corrected
            assert response.json()['data']['result']['revenue'] == '2.000000000000000001'
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
