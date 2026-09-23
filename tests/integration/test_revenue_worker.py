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


def test_wrong_calculation_identity_cannot_be_published(database):  # noqa: F811
    versions = store.Versions('code', 'config', '0')
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        report = worker.run_prime(conn, 'obex', compute=lambda *a, **kw: example(2),
                                  capture=lambda: versions)
        assert report['status'] == 'failed'
        assert store.read(conn, 'obex') is None


def test_weekend_sofr_defers_without_failed_attempt_and_monday_recovers(database):  # noqa: F811
    from datetime import UTC, date, datetime

    versions = store.Versions('code', 'config', '0')
    calls = []

    def prepare(*args):
        calls.append('prepare')
        return None

    def compute(config, month, *, as_of):
        from settle.domain.period import Period
        calls.append(as_of)
        return replace(example(), prime_id='spark', period=Period.from_month(month, {}, as_of=as_of))

    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        report = worker.run_prime(
            conn, 'spark', now=datetime(2026, 9, 13, 16, tzinfo=UTC),
            start=date(2026, 9, 11), end=date(2026, 9, 11),
            capture=lambda: versions, prepare=prepare, compute=compute)
        assert report['status'] == 'ok'
        assert report['results'] == [{'cutoff': '2026-09-11', 'status': 'deferred',
                                      'reason': 'reference_rate_not_due'}]
        assert not calls
        assert conn.execute('SELECT count(*) FROM revenue_attempts').fetchone()[0] == 0
        report = worker.run_prime(
            conn, 'spark', now=datetime(2026, 9, 14, 20, 17, tzinfo=UTC),
            start=date(2026, 9, 11), end=date(2026, 9, 13),
            capture=lambda: versions, prepare=prepare, compute=compute, attempts=1)
        assert report['status'] == 'ok'
        assert [r['status'] for r in report['results']] == ['succeeded'] * 3
        assert conn.execute('SELECT count(*) FROM revenue_results').fetchone()[0] == 3


def test_missing_only_backfill_keeps_published_revision_across_code_changes(database):  # noqa: F811
    pnl = example()
    old = store.Versions('published-code', 'config', '0')
    new = replace(old, code='backfill-code')
    previous = pnl.as_of - timedelta(days=1)
    calls = []

    def compute(config, month, *, as_of):
        from settle.domain.period import Period
        calls.append(as_of)
        return replace(pnl, period=Period.from_month(month, {}, as_of=as_of))

    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        with conn.transaction():
            original_revision = store.publish(conn, pnl, old)
        report = worker.run_prime(conn, 'obex', start=previous, end=pnl.as_of,
                                  missing_only=True, compute=compute, capture=lambda: new)
        assert report['status'] == 'ok'
        assert calls == [previous]
        assert store.read(conn, 'obex', cutoff=pnl.as_of)['revision_id'] == original_revision
        assert len(store.revisions(conn, 'obex', pnl.as_of)) == 1
        assert store.read(conn, 'obex', cutoff=previous)['code_version'] == new.code
        report = worker.run_prime(conn, 'obex', start=previous, end=pnl.as_of,
                                  missing_only=True, compute=compute, capture=lambda: new)
        assert report['results'] == []
        assert calls == [previous]
