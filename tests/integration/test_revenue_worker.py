from dataclasses import replace
from datetime import timedelta

import psycopg

from settle.revenue import store, worker
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.integration.test_revenue_store import example


def test_retry_atomic_publication_and_idempotent_restart(database):  # noqa: F811
    pnl = example()
    versions = store.Versions('code', 'config', '0')
    calls = []
    def compute(*args, **kwargs):
        calls.append(kwargs['as_of'])
        if len(calls) == 1:
            raise ConnectionError('retryable failure')
        return pnl
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        report = worker.run_prime(conn, 'obex', compute=compute, capture=lambda: versions, pause=lambda _: None)
        assert report['status'] == 'ok'
        assert conn.execute('SELECT status FROM revenue_attempts ORDER BY started_at').fetchall() == [('failed',), ('succeeded',)]
        assert store.read(conn, 'obex') is not None
        report = worker.run_prime(conn, 'obex', compute=compute, capture=lambda: versions)
        assert report['results'][0]['status'] == 'reused'
        assert len(calls) == 2


def test_overlap_abandonment_and_version_change_never_publish(database):  # noqa: F811
    import uuid
    pnl = example()
    versions = store.Versions('code', 'config', '0')
    with psycopg.connect(database, autocommit=True) as first, psycopg.connect(database, autocommit=True) as second:
        store.apply_schema(first)
        first.execute('SELECT pg_advisory_lock(%s)', (worker.lock_key('obex'),))
        assert worker.run_prime(second, 'obex')['status'] == 'already_running'
        first.execute('''INSERT INTO revenue_attempts(attempt_id,prime,cutoff,versions,status)
            VALUES (%s,'obex',%s,'{}','running')''', (uuid.uuid4(), pnl.as_of))
        first.execute('SELECT pg_advisory_unlock(%s)', (worker.lock_key('obex'),))
        versions_read = iter([versions, replace(versions, inputs='changed')])
        report = worker.run_prime(second, 'obex', compute=lambda *a, **kw: pnl,
                                  capture=lambda: next(versions_read), attempts=1)
        assert report['status'] == 'failed'
        assert store.read(second, 'obex') is None
        statuses = second.execute('SELECT status FROM revenue_attempts ORDER BY started_at').fetchall()
        assert statuses == [('abandoned',), ('failed',)]


def test_failed_historical_day_does_not_block_other_dates(database):  # noqa: F811
    pnl = example()
    versions = store.Versions('code', 'config', '0')
    def compute(*args, **kwargs):
        if kwargs['as_of'] < pnl.as_of:
            raise ValueError('unsupported historical monthly-only reward')
        return pnl
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        report = worker.run_prime(conn, 'obex', start=pnl.as_of-timedelta(days=1), end=pnl.as_of,
                                  compute=compute, capture=lambda: versions)
        assert report['status'] == 'failed'
        assert store.read(conn, 'obex')['cutoff'] == pnl.as_of.isoformat()
