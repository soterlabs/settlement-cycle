"""Nightly catch-up retries only missing or failed daily cutoffs."""
import importlib.util
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest


def script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[2] / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


catchup = script('revenue_catchup')
TODAY = date(2026, 11, 24)


def d(day):
    return date(2026, 11, day)


def test_targets_missing_failed_and_abandoned_newest_first():
    published = {d(day) for day in range(10, 24)} - {d(20), d(15)}
    latest = {d(22): 'failed', d(21): 'succeeded', d(18): 'abandoned', d(15): 'running', d(12): 'failed'}
    # 20 is missing; 15 is missing but someone is computing it; 23 published normally.
    assert catchup.targets(published, latest, TODAY, 14) == [d(22), d(20), d(18), d(12)]


def test_window_is_completed_days_only():
    assert catchup.targets(set(), {}, TODAY, 2) == [d(23), d(22)]
    with pytest.raises(ValueError):
        catchup.targets(set(), {}, TODAY, 0)


class LockConn:
    """Records advisory lock calls; ``busy`` makes pg_try_advisory_lock fail."""

    def __init__(self, log, busy=False):
        self.log, self.busy = log, busy

    def execute(self, sql, params=None):
        self.log.append(sql.split('(')[0].split()[-1])
        busy = self.busy

        class Result:
            def fetchone(_):
                return (not busy,)
        return Result()


def harness(monkeypatch, states):
    """states: (published, latest) returned by successive load_state calls."""
    calls, worked, locks = [], [], []

    @contextmanager
    def connect():
        yield object()

    def load_state(conn, prime, first, last):
        calls.append((prime, first, last))
        return states.pop(0)

    def worker(conn, prime, *, start, end, **kwargs):
        worked.append((prime, start))
        status = 'failed' if start == d(20) else 'succeeded'
        return {'prime': prime, 'status': 'ok', 'results': [{'cutoff': str(start), 'status': status}]}

    def replay(connect_, prime, start, end, run):
        # The real driver: one cutoff, run(conn, prime, start=, end=, ...) under a fresh session.
        report = run(LockConn(locks), prime, start=start, end=end, require_views=True, wait_for_lock=False,
                     now=None)
        results = report['results']
        return {'counts': {r['status']: 1 for r in results},
                'failed': [r for r in results if r['status'] == 'failed']}

    monkeypatch.setattr(catchup, 'load_state', load_state)
    return connect, replay, worker, calls, worked, locks


def test_rechecks_each_cutoff_under_the_prime_lock(monkeypatch):
    planning = ({d(day) for day in range(10, 24)} - {d(20), d(22)}, {d(22): 'failed'})
    states = [planning,
              ({d(22)}, {d(22): 'succeeded'}),  # the 20:17 worker published it while we waited
              (set(), {})]                      # 20 still missing
    connect, replay, worker, calls, worked, locks = harness(monkeypatch, states)
    reports = catchup.catch_up(connect, ['spark'], TODAY, 14, replay=replay, worker=worker)
    assert calls == [('spark', d(10), d(23)), ('spark', d(22), d(22)), ('spark', d(20), d(20))]
    assert worked == [('spark', d(20))]
    assert locks == ['pg_try_advisory_lock', 'pg_advisory_unlock'] * 2  # checked while holding the lock
    assert reports == [{'prime': 'spark', 'status': 'failed', 'planned': ['2026-11-22', '2026-11-20'],
                        'skipped': ['2026-11-22'], 'failed': [{'cutoff': '2026-11-20', 'status': 'failed'}]}]


def test_guarded_run_waits_when_the_prime_lock_is_held(monkeypatch):
    monkeypatch.setattr(catchup, 'load_state', lambda *a: pytest.fail('must not read state without the lock'))
    log = []
    report = catchup.guarded_run(LockConn(log, busy=True), 'spark', start=d(20), end=d(20),
                                 run=lambda *a, **k: pytest.fail('must not run'))
    assert report['status'] == 'already_running' and log == ['pg_try_advisory_lock']
    with pytest.raises(ValueError):
        catchup.guarded_run(LockConn(log), 'spark', start=d(19), end=d(20), run=None)


def test_dry_run_and_nothing_to_do_never_replay(monkeypatch):
    full = ({d(day) for day in range(10, 24)}, {})
    connect, replay, worker, _, worked, _ = harness(monkeypatch, [full, (set(), {})])
    reports = catchup.catch_up(connect, ['grove', 'osero'], TODAY, 14, replay=replay, dry_run=True,
                               worker=worker)
    assert worked == []
    assert reports == [{'prime': 'grove', 'status': 'ok', 'planned': []},
                       {'prime': 'osero', 'status': 'ok', 'planned': [str(d(day)) for day in range(23, 9, -1)]}]


class RequestConn:
    """Fake queue: ``statuses`` answers successive status re-checks (default 'running')."""

    def __init__(self, db):
        self.db = db

    def execute(self, sql, params=None):
        sql = ' '.join(sql.split())
        self.db['log'].append((sql[:50], params))
        db = self.db

        class Result:
            def fetchall(_):
                return db['pending'] if sql.startswith('SELECT request_id') else []

            def fetchone(_):
                if sql.startswith('UPDATE revenue_replay_requests SET status=\'running\''):
                    return (params[0],) if db.get('claimable', True) else None
                if sql.startswith('SELECT status'):
                    return (db['statuses'].pop(0) if db.get('statuses') else 'running',)
                return None
        return Result()


def requests_harness(**db):
    db = {'log': [], 'pending': [(7, 'spark', d(1), d(4), d(2), [{'cutoff': '2026-11-03', 'status': 'failed'}])],
          **db}

    @contextmanager
    def connect():
        yield RequestConn(db)

    replays = []

    def replay(connect_, prime, start, end):
        replays.append(start)
        if start == d(1):
            raise ValueError('replay only completed UTC days')
        return {'failed': []}

    return db, connect, replay, replays


def test_requests_resume_from_progress_and_record_failures():
    db, connect, replay, replays = requests_harness()
    reports = catchup.run_requests(connect, replay=replay)
    assert replays == [d(2), d(1)]  # resumed at next_cutoff, newest first
    progress = [params for sql, params in db['log'] if sql.startswith('UPDATE revenue_replay_requests SET n')]
    assert [p[0] for p in progress] == [d(1), date(2026, 10, 31)]
    assert reports[0]['status'] == 'failed' and [f['cutoff'] for f in reports[0]['failed']] == [
        '2026-11-03', '2026-11-01']
    final = db['log'][-1]
    assert final[1] == ('failed', 7)


def test_cancelled_requests_stop_and_stay_cancelled():
    # Cancelled before it was claimed: never runs.
    db, connect, replay, replays = requests_harness(claimable=False)
    assert catchup.run_requests(connect, replay=replay) == [] and replays == []
    # Cancelled while running: stops before the next cutoff, status untouched.
    db, connect, replay, replays = requests_harness(statuses=['running', 'cancelled'])
    reports = catchup.run_requests(connect, replay=replay)
    assert replays == [d(2)] and reports[0]['status'] == 'cancelled'
    assert not any(sql.startswith('UPDATE revenue_replay_requests SET status=%s') for sql, _ in db['log'])


def test_request_validation():
    request = importlib.util.spec_from_file_location(
        'request_revenue_replay', Path(__file__).parents[2] / 'scripts/request_revenue_replay.py')
    module = importlib.util.module_from_spec(request)
    request.loader.exec_module(module)
    module.validate('spark', d(1), d(23), TODAY, ('spark',))
    for args, message in ((('spark', d(5), d(1)), 'must not be after'), (('spark', d(1), d(24)), 'completed'),
                          (('nope', d(1), d(2)), 'unknown prime')):
        with pytest.raises(ValueError, match=message):
            module.validate(*args, TODAY, ('spark',))
