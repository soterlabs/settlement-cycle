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


def test_future_attempt_metadata_is_invalid():
    data = payload()
    data['primes']['grove']['latest_attempt']['cutoff'] = '2026-09-18'
    assert not assess(data, datetime(2026, 9, 17, 3, tzinfo=UTC))['ready']


def test_weekend_sofr_delay_is_not_a_missed_run_but_other_primes_remain_due():
    data = payload('2026-09-18')
    for prime in ['grove', 'spark']:
        data['primes'][prime] = {'actual_cutoff': '2026-09-17',
                                'latest_attempt': {'cutoff': '2026-09-18', 'status': 'failed'}}
    report = assess(data, datetime(2026, 9, 20, 3, tzinfo=UTC))
    assert report['ready']
    assert report['expected_cutoff_by_prime']['spark'] == '2026-09-17'
    data['primes']['obex']['actual_cutoff'] = '2026-09-17'
    assert assess(data, datetime(2026, 9, 20, 3, tzinfo=UTC))['failures'] == {'obex': 'missing_due_cutoff'}


def test_monday_evening_catchup_is_required_after_deadline():
    data = payload('2026-09-20')
    for prime in ['grove', 'spark']:
        data['primes'][prime]['actual_cutoff'] = '2026-09-17'
    report = assess(data, datetime(2026, 9, 22, 3, tzinfo=UTC))
    assert report['expected_cutoff_by_prime']['spark'] == '2026-09-20'
    assert set(report['failures']) == {'grove', 'spark'}


def test_monday_morning_does_not_require_evening_sofr_catchup():
    data = payload('2026-09-19')
    for prime in ['grove', 'spark']:
        data['primes'][prime] = {'actual_cutoff': '2026-09-17',
                                'latest_attempt': {'cutoff': '2026-09-18', 'status': 'failed'}}
    assert assess(data, datetime(2026, 9, 21, 15, tzinfo=UTC))['ready']
