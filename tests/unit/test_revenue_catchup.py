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


def harness(monkeypatch, states):
    """states: list of (published, latest) returned by successive load_state calls."""
    calls, replays = [], []

    @contextmanager
    def connect():
        yield object()

    def load_state(conn, prime, first, last):
        calls.append((prime, first, last))
        return states.pop(0)

    def replay(connect_, prime, start, end):
        assert start == end
        replays.append((prime, start))
        failed = [{'cutoff': str(start), 'status': 'failed'}] if start == d(20) else []
        return {'failed': failed}

    monkeypatch.setattr(catchup, 'load_state', load_state)
    return connect, replay, calls, replays


def test_rechecks_each_cutoff_and_reports_failures(monkeypatch):
    planning = ({d(day) for day in range(10, 24)} - {d(20), d(22)}, {d(22): 'failed'})
    states = [planning,
              ({d(22)}, {d(22): 'succeeded'}),  # published since planning
              (set(), {})]  # 20 still missing
    connect, replay, calls, replays = harness(monkeypatch, states)
    reports = catchup.catch_up(connect, ['spark'], TODAY, 14, replay=replay)
    assert calls[0] == ('spark', d(10), d(23))
    assert replays == [('spark', d(20))]
    assert reports == [{'prime': 'spark', 'status': 'failed', 'planned': ['2026-11-22', '2026-11-20'],
                        'skipped': ['2026-11-22'], 'failed': [{'cutoff': '2026-11-20', 'status': 'failed'}]}]


def test_dry_run_and_nothing_to_do_never_replay(monkeypatch):
    full = ({d(day) for day in range(10, 24)}, {})
    connect, replay, _, replays = harness(monkeypatch, [full, (set(), {})])
    reports = catchup.catch_up(connect, ['grove', 'osero'], TODAY, 14, replay=replay, dry_run=True)
    assert replays == []
    assert reports == [{'prime': 'grove', 'status': 'ok', 'planned': []},
                       {'prime': 'osero', 'status': 'ok', 'planned': [str(d(day)) for day in range(23, 9, -1)]}]


class RequestConn:
    def __init__(self, db):
        self.db = db

    def execute(self, sql, params=None):
        sql = ' '.join(sql.split())
        self.db['log'].append((sql[:50], params))

        class Result:
            def fetchall(_):
                return self.db['pending'] if sql.startswith('SELECT request_id') else []
        return Result()


def test_requests_resume_from_progress_and_record_failures():
    db = {'log': [], 'pending': [(7, 'spark', d(1), d(4), d(2), [{'cutoff': '2026-11-03', 'status': 'failed'}])]}

    @contextmanager
    def connect():
        yield RequestConn(db)

    replays = []

    def replay(connect_, prime, start, end):
        replays.append(start)
        if start == d(1):
            raise ValueError('replay only completed UTC days')
        return {'failed': []}

    reports = catchup.run_requests(connect, replay=replay)
    assert replays == [d(2), d(1)]  # resumed at next_cutoff, newest first
    progress = [params for sql, params in db['log'] if sql.startswith('UPDATE revenue_replay_requests SET n')]
    assert [p[0] for p in progress] == [d(1), date(2026, 10, 31)]
    assert reports[0]['status'] == 'failed' and [f['cutoff'] for f in reports[0]['failed']] == [
        '2026-11-03', '2026-11-01']
    assert db['log'][-1][1] == ('failed', 7)


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
