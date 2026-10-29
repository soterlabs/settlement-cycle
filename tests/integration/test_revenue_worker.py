from dataclasses import dataclass, field, replace
from datetime import timedelta

import psycopg
import pytest

from settle.revenue import store, worker
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.integration.test_revenue_store import Pnl, example


@dataclass
class ViewedPnl(Pnl):
    venue_views: dict = field(default_factory=lambda: {
        'schema_version': '2.0', 'settlement': [], 'reporting': [],
    })


def with_views(pnl):
    return ViewedPnl(pnl.prime_id, pnl.period, pnl.pin_blocks_som, pnl.revenue)


def test_view_backfill_upgrades_legacy_and_preserves_compatible_revisions(database):  # noqa: F811
    older, current = example(2), with_views(example())
    versions = store.Versions('new', 'cfg', '0')
    calls = []
    def compute(*args, **kwargs):
        calls.append(kwargs['as_of'])
        return with_views(older)
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        # Even a same-code legacy result must be recomputed, not reused.
        old_id = store.publish(conn, older, versions, input_provenance={'manual_input_revision': '0'})
        kept = store.publish(conn, current, replace(versions, code='previous'))
        report = worker.run_prime(conn, 'obex', start=older.as_of, end=current.as_of,
                                  missing_views_only=True, newest_first=True,
                                  compute=compute, capture=lambda: versions, prepare=lambda *_: None)
        assert report['status'] == 'ok'
        assert calls == [older.as_of]
        assert store.read(conn, 'obex', cutoff=current.as_of)['revision_id'] == kept
        assert store.read(conn, 'obex', cutoff=older.as_of)['revision_id'] != old_id
        assert store.read(conn, 'obex', revision=old_id)['result'].get('venue_views') is None
        report = worker.run_prime(conn, 'obex', start=older.as_of, end=current.as_of,
                                  missing_views_only=True, compute=compute,
                                  capture=lambda: replace(versions, code='even-newer'))
        assert report['results'] == []
        # A later incompatible correction must not hide behind an older v2 row.
        store.publish(conn, older, replace(versions, inputs='legacy-correction'))
        assert store.venue_view_dates(conn, 'obex') == {current.as_of}


def test_require_views_refuses_incomplete_computation(database):  # noqa: F811
    pnl = example()
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        report = worker.run_prime(conn, 'obex', start=pnl.as_of, end=pnl.as_of,
                                  compute=lambda *_args, **_kw: pnl, require_views=True,
                                  capture=lambda: store.Versions('code', 'cfg', '0'),
                                  prepare=lambda *_: None, attempts=1)
        assert report['status'] == 'failed'
        assert store.read(conn, 'obex') is None


def test_newest_first_publishes_latest_before_older_history(database):  # noqa: F811
    older, current = with_views(example(2)), with_views(example())
    calls = []
    def compute(*_args, **kwargs):
        calls.append(kwargs['as_of'])
        return current if kwargs['as_of'] == current.as_of else older
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        report = worker.run_prime(conn, 'obex', start=older.as_of, end=current.as_of,
                                  newest_first=True, require_views=True, compute=compute,
                                  capture=lambda: store.Versions('code', 'cfg', '0'),
                                  prepare=lambda *_: None)
        assert report['status'] == 'ok' and calls == [current.as_of, older.as_of]
        assert store.read(conn, 'obex')['cutoff'] == str(current.as_of)


def test_daily_run_publishes_current_cutoff_before_retrying_failed_history(database, monkeypatch):  # noqa: F811
    from datetime import UTC, datetime

    from settle.domain.period import Period

    current = with_views(example())
    oldest = current.as_of - timedelta(days=2)
    today = current.as_of + timedelta(days=1)
    versions = store.Versions('code', 'cfg', '0')
    monkeypatch.setenv('REVENUE_START_DATE', str(oldest))
    calls = []

    def compute(config, month, *, as_of):
        calls.append(as_of)
        if as_of == oldest:
            raise ValueError('unsupported historical input')
        return replace(current, period=Period.from_month(month, {}, as_of=as_of))

    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        report = worker.run_prime(
            conn, 'obex', now=datetime.combine(today, datetime.min.time(), UTC).replace(hour=21),
            require_views=True, compute=compute, capture=lambda: versions, prepare=lambda *_: None)
        assert calls == [current.as_of, current.as_of - timedelta(days=1), oldest]
        assert [r['status'] for r in report['results']] == ['succeeded', 'succeeded', 'failed']
        assert store.read(conn, 'obex')['cutoff'] == str(current.as_of)
        # A failing historical day remains visible; it never invalidates the
        # successful current publication or suppresses recovery on the next tick.
        assert report['status'] == 'failed'
        report = worker.run_prime(
            conn, 'obex', now=datetime.combine(today, datetime.min.time(), UTC).replace(hour=22),
            require_views=True, compute=compute, capture=lambda: versions, prepare=lambda *_: None)
        assert [r['status'] for r in report['results']] == ['reused', 'failed']
        assert calls == [current.as_of, current.as_of - timedelta(days=1), oldest, oldest]


def test_daily_weekend_prioritizes_last_eligible_cutoff_before_older_gaps(database, monkeypatch):  # noqa: F811
    from datetime import UTC, date, datetime

    from settle.domain.period import Period

    monkeypatch.setenv('REVENUE_START_DATE', '2026-09-10')
    calls = []

    def compute(config, month, *, as_of):
        calls.append(as_of)
        return replace(with_views(example()), prime_id='spark',
                       period=Period.from_month(month, {}, as_of=as_of))

    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        report = worker.run_prime(
            conn, 'spark', now=datetime(2026, 9, 13, 20, 17, tzinfo=UTC),
            compute=compute, capture=lambda: store.Versions('code', 'cfg', '0'),
            prepare=lambda *_: None, require_views=True)
        assert calls == [date(2026, 9, 10)]
        assert [r['status'] for r in report['results']] == ['deferred', 'deferred', 'succeeded']
        assert store.read(conn, 'spark')['cutoff'] == '2026-09-10'


def test_backfill_waits_for_the_shared_prime_lock_instead_of_skipping(database):  # noqa: F811
    pnl = with_views(example())
    with psycopg.connect(database, autocommit=True) as holder, psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        holder.execute('SELECT pg_advisory_lock(%s)', (worker.lock_key('obex'),))
        conn.execute("SET lock_timeout = '50ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            worker.run_prime(conn, 'obex', start=pnl.as_of, end=pnl.as_of, wait_for_lock=True)
        assert conn.execute('SELECT COUNT(*) FROM revenue_attempts').fetchone()[0] == 0
        holder.execute('SELECT pg_advisory_unlock(%s)', (worker.lock_key('obex'),))
        conn.execute('SET lock_timeout = 0')
        report = worker.run_prime(conn, 'obex', start=pnl.as_of, end=pnl.as_of,
                                  wait_for_lock=True, require_views=True,
                                  compute=lambda *_args, **_kw: pnl,
                                  capture=lambda: store.Versions('code', 'cfg', '0'),
                                  prepare=lambda *_: None)
        assert report['status'] == 'ok'


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
