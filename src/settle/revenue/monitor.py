"""Independent daily completion check, run after the worker's deadline + grace."""
import json
import os
from datetime import UTC, date, datetime, timedelta

import requests

from settle.domain.config import load_prime_by_id

from .reference_rates import available_cutoff
from .verification import PRIMES


def expected_cutoff(now):
    """Most recent 20:17 UTC run whose six-hour deadline + 30m grace elapsed."""
    if now.tzinfo is None:
        raise ValueError('monitor time must be timezone-aware')
    eligible = now.astimezone(UTC) - timedelta(hours=6, minutes=30)
    scheduled = eligible.replace(hour=20, minute=17, second=0, microsecond=0)
    if scheduled > eligible:
        scheduled -= timedelta(days=1)
    return scheduled.date() - timedelta(days=1)


def assess(payload, now):
    expected = expected_cutoff(now)
    failures = {}
    expected_by_prime = {}
    if not isinstance(payload, dict) or payload.get('cadence') != 'daily':
        raise ValueError('Invalid revenue status response')
    primes = payload.get('primes')
    if not isinstance(primes, dict):
        raise ValueError('Missing prime statuses')
    for prime in PRIMES:
        try:
            config = load_prime_by_id(prime)
            scheduled = datetime.combine(expected + timedelta(days=1), datetime.min.time(), UTC).replace(
                hour=20, minute=17)
            due = available_cutoff(config, expected, scheduled)
            ready_through = available_cutoff(config, now.astimezone(UTC).date() - timedelta(days=1), scheduled)
            expected_by_prime[prime] = str(due)
            status = primes[prime]
            cutoff = date.fromisoformat(status['actual_cutoff'])
            if not due <= cutoff < now.astimezone(UTC).date():
                failures[prime] = 'missing_due_cutoff'
                continue
            attempt = status.get('latest_attempt')
            if not isinstance(attempt, dict):
                failures[prime] = 'missing_attempt'
                continue
            attempted = date.fromisoformat(attempt['cutoff'])
            if attempted >= now.astimezone(UTC).date():
                failures[prime] = 'invalid_attempt_cutoff'
                continue
            state = attempt['status']
            if state not in {'succeeded', 'running', 'failed', 'abandoned'}:
                failures[prime] = 'invalid_attempt_status'
            elif due <= attempted <= ready_through and state in {'failed', 'abandoned'}:
                failures[prime] = 'failed_attempt'
            elif attempted == due and state == 'running':
                failures[prime] = 'deadline_exceeded'
        except (KeyError, TypeError, ValueError):
            failures[prime] = 'missing_or_invalid_status'
    return {'expected_cutoff': str(expected), 'expected_cutoff_by_prime': expected_by_prime,
            'ready': not failures, 'failures': failures}


def main():
    base = os.environ.get('REVENUE_API_URL', 'https://settle-api-production.up.railway.app')
    try:
        response = requests.get(base.rstrip('/') + '/v1/revenue/status', timeout=(10, 30))
        # 503 is the API's current-calendar-day freshness response. At 03:00
        # the evening job for that new day is not due yet; assess its payload
        # against the schedule deadline rather than treating every 503 as late.
        if response.status_code not in {200, 503}:
            raise RuntimeError('Revenue status endpoint unavailable')
        report = assess(response.json(), datetime.now(UTC))
    except Exception as exc:
        report = {'ready': False, 'error_type': type(exc).__name__}
    print(json.dumps(report, sort_keys=True))
    if not report['ready']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
