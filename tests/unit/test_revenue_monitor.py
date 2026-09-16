from datetime import UTC, datetime

import pytest

from settle.revenue.monitor import PRIMES, assess, expected_cutoff, main


def payload(cutoff='2026-09-15'):
    return {'cadence': 'daily', 'ready': False, 'primes': {
        p: {'actual_cutoff': cutoff, 'latest_attempt': {'cutoff': cutoff, 'status': 'succeeded'}}
        for p in PRIMES}}


@pytest.mark.parametrize('now,expected', [
    ('2026-09-17T02:46:59+00:00', '2026-09-14'),
    ('2026-09-17T02:47:00+00:00', '2026-09-15'),
    ('2026-09-17T03:00:00+00:00', '2026-09-15'),
    ('2026-10-01T03:00:00+00:00', '2026-09-29'),
    ('2026-10-02T03:00:00+00:00', '2026-09-30'),
    ('2028-03-01T03:00:00+00:00', '2028-02-28'),
])
def test_deadline_and_month_boundaries(now, expected):
    assert str(expected_cutoff(datetime.fromisoformat(now))) == expected


def test_api_calendar_staleness_is_not_a_missed_scheduled_run():
    assert assess(payload(), datetime(2026, 9, 17, 3, tzinfo=UTC))['ready']
    assert not assess(payload(), datetime(2026, 9, 18, 3, tzinfo=UTC))['ready']


@pytest.mark.parametrize('change', ['missing', 'failed', 'running', 'abandoned', 'bad_status', 'future'])
def test_incomplete_fleet_fails_closed(change):
    data = payload()
    status = data['primes']['grove']
    if change == 'missing': del data['primes']['grove']
    elif change == 'future': status['actual_cutoff'] = '2026-09-18'
    else: status['latest_attempt']['status'] = change
    report = assess(data, datetime(2026, 9, 17, 3, tzinfo=UTC))
    assert not report['ready'] and 'grove' in report['failures']


def test_newer_job_may_still_be_running_before_its_deadline():
    data = payload()
    data['primes']['grove']['latest_attempt'] = {'cutoff': '2026-09-16', 'status': 'running'}
    assert assess(data, datetime(2026, 9, 17, 21, tzinfo=UTC))['ready']


def test_monitor_accepts_valid_503_payload_and_sanitizes_failures(monkeypatch, capsys):
    from settle.revenue import monitor
    class Response:
        status_code = 503
        def json(self): return payload()
    monkeypatch.setattr(monitor.requests, 'get', lambda *a, **kw: Response())
    monkeypatch.setattr(monitor, 'assess', lambda *a: {'ready': True})
    main()
    def unavailable(*a, **kw): raise RuntimeError('private-url-token')
    monkeypatch.setattr(monitor.requests, 'get', unavailable)
    with pytest.raises(SystemExit, match='1'):
        main()
    assert 'private-url-token' not in capsys.readouterr().out
