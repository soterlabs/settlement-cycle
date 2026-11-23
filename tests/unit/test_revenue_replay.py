"""Stale-cutoff replay: one cutoff per lock, newest first, never in the daily window."""
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock

import pytest

from settle.revenue import replay as module


class Clock:
    def __init__(self, start):
        self.t = start
        self.slept = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept += seconds
        self.t += timedelta(seconds=seconds)


def harness(start, outcomes=None):
    clock_ = Clock(start)
    calls, connections = [], []

    @contextmanager
    def connect():
        connections.append('open')
        yield object()
        connections.append('closed')

    def run(conn, prime, **kwargs):
        calls.append((prime, kwargs['start'], kwargs['end'], kwargs['now'], kwargs['wait_for_lock'],
                      kwargs['require_views']))
        assert kwargs['start'] == kwargs['end']  # one cutoff per lock acquisition
        assert not module.in_window(kwargs['now'], module.DEFAULT_PAUSE)
        clock_.t += timedelta(minutes=45)  # a slow Spark cutoff
        status = (outcomes or {}).get(kwargs['start'], 'succeeded')
        return {'prime': prime, 'status': 'failed' if status == 'failed' else 'ok',
                'results': [{'cutoff': str(kwargs['start']), 'status': status}]}

    return clock_, calls, connections, connect, run


def test_newest_first_one_cutoff_per_lock_with_views_required():
    clock_, calls, connections, connect, run = harness(datetime(2026, 11, 24, 4, 0, tzinfo=UTC))
    summary = module.replay(connect, 'spark', date(2026, 10, 29), date(2026, 10, 31),
                            run=run, now=clock_.now, sleep=clock_.sleep)
    assert [c[1] for c in calls] == [date(2026, 10, 31), date(2026, 10, 30), date(2026, 10, 29)]
    assert all(c[4] and c[5] for c in calls)  # wait for the lock; require both API views
    assert connections == ['open', 'closed'] * 3  # the lock session ends after every cutoff
    assert summary == {'prime': 'spark', 'from': '2026-10-29', 'to': '2026-10-31', 'status': 'ok',
                       'counts': {'succeeded': 3}, 'failed': []}


def test_never_starts_a_cutoff_in_the_scheduled_daily_window():
    # 18:30 start: two 45-minute cutoffs would cross 19:00 -> the second waits until 03:00.
    clock_, calls, _, connect, run = harness(datetime(2026, 11, 24, 18, 30, tzinfo=UTC))
    module.replay(connect, 'grove', date(2026, 11, 1), date(2026, 11, 3), run=run,
                  now=clock_.now, sleep=clock_.sleep)
    starts = [c[3] for c in calls]
    assert starts[0] == datetime(2026, 11, 24, 18, 30, tzinfo=UTC)
    assert starts[1] == datetime(2026, 11, 25, 3, 0, tzinfo=UTC)  # resumed after the watchdog window
    assert starts[2] == datetime(2026, 11, 25, 3, 45, tzinfo=UTC)


def test_failures_are_reported_and_later_cutoffs_still_run():
    clock_, calls, _, connect, run = harness(datetime(2026, 11, 24, 5, 0, tzinfo=UTC),
                                             {date(2026, 10, 30): 'failed'})
    summary = module.replay(connect, 'osero', date(2026, 10, 29), date(2026, 10, 31), run=run,
                            now=clock_.now, sleep=clock_.sleep)
    assert len(calls) == 3
    assert summary['status'] == 'failed'
    assert summary['failed'] == [{'cutoff': '2026-10-30', 'status': 'failed'}]


def test_window_parsing_and_guards():
    assert module.parse_window('19:00-03:00') == (clock(19), clock(3))
    assert module.in_window(datetime(2026, 11, 24, 2, 59, tzinfo=UTC), module.DEFAULT_PAUSE)
    assert not module.in_window(datetime(2026, 11, 24, 3, 0, tzinfo=UTC), module.DEFAULT_PAUSE)
    assert module.in_window(datetime(2026, 11, 24, 12, 0, tzinfo=UTC), (clock(9), clock(17)))
    with pytest.raises(ValueError):
        module.parse_window('19:00-19:00')
    clock_, _, _, connect, run = harness(datetime(2026, 11, 24, 5, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match='completed UTC days'):
        module.replay(connect, 'spark', date(2026, 11, 23), date(2026, 11, 24), run=run, now=clock_.now)
    with pytest.raises(ValueError, match='from must not be after'):
        module.replay(connect, 'spark', date(2026, 11, 3), date(2026, 11, 1), run=run, now=clock_.now)
