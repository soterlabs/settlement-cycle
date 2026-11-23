"""Replay retained daily cutoffs on the current code, one cutoff per prime lock.

Use this when published observations are stale or incompatible: for example
computed before venues were added to a prime's configuration, so their
``venue_breakdown`` silently lacks allocations the current code includes. The
normal worker's ``--missing-views-only`` mode keeps such observations because
their views exist; an explicit worker window replays them but holds the prime's
advisory lock for the whole window, which would make the scheduled 20:17 UTC
job report ``already_running`` and skip that day's publication.

This driver instead calls :func:`settle.revenue.worker.run_prime` for one cutoff
at a time, newest first, releasing the lock after each, and does not START a
cutoff inside the scheduled daily window (default 19:00-03:00 UTC, covering the
20:17 start and its six-hour watchdog). Each cutoff keeps the worker's existing
behaviour: reference-rate gates, bounded retries, immutable append-only
revisions, reuse of an identical current revision, required venue views and
financial-reporting projection. Nothing is deleted or rewritten.

    python -m settle.revenue.replay --prime spark --from 2026-10-01 --to 2026-11-08
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock

from .worker import deadline, run_prime

_log = logging.getLogger(__name__)
DEFAULT_PAUSE = (clock(19, 0), clock(3, 0))


def parse_window(text):
    start, end = (clock.fromisoformat(part) for part in text.split('-'))
    if start == end:
        raise ValueError('pause window must not be empty')
    return start, end


def in_window(now, window):
    start, end = window
    t = now.astimezone(UTC).time()
    return start <= t < end if start < end else (t >= start or t < end)


def seconds_until_open(now, window):
    """Seconds until ``now`` leaves the pause window (0 when outside it)."""
    if not in_window(now, window):
        return 0
    now = now.astimezone(UTC)
    opens = datetime.combine(now.date(), window[1], UTC)
    if opens <= now:
        opens += timedelta(days=1)
    return (opens - now).total_seconds()


def replay(connect, prime, start, end, *, window=DEFAULT_PAUSE, run=run_prime,
           now=lambda: datetime.now(UTC), sleep=time.sleep):
    if start > end:
        raise ValueError('from must not be after to')
    if end >= now().astimezone(UTC).date():
        raise ValueError('replay only completed UTC days')
    reports = []
    cutoff = end
    while cutoff >= start:
        while (wait := seconds_until_open(now(), window)) > 0:
            _log.info('replay paused for the scheduled daily window prime=%s next=%s', prime, cutoff)
            sleep(min(wait, 300))
        with connect() as conn:
            report = run(conn, prime, start=cutoff, end=cutoff, require_views=True,
                         wait_for_lock=True, now=now())
        reports.append(report)
        _log.info('replay cutoff prime=%s cutoff=%s report=%s', prime, cutoff, json.dumps(report))
        cutoff -= timedelta(days=1)
    results = [r for report in reports for r in report['results']]
    failed = [r for r in results if r['status'] == 'failed' or r.get('financial_status') == 'failed']
    return {'prime': prime, 'from': str(start), 'to': str(end),
            'status': 'failed' if failed or any(r['status'] == 'failed' for r in reports) else 'ok',
            'counts': {s: sum(r['status'] == s for r in results)
                       for s in sorted({r['status'] for r in results})},
            'failed': failed}


def main():
    from dotenv import load_dotenv

    from ..store.db import connect
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--prime', required=True)
    parser.add_argument('--from', dest='start', type=date.fromisoformat, required=True)
    parser.add_argument('--to', dest='end', type=date.fromisoformat, required=True)
    parser.add_argument('--pause-utc', type=parse_window, default=DEFAULT_PAUSE,
                        help='Do not start a cutoff in this UTC window (default 19:00-03:00)')
    args = parser.parse_args()
    os.environ['SETTLE_REQUIRE_POSTGRES'] = '1'
    logging.basicConfig(level=logging.INFO)
    summary = replay(lambda: connect(autocommit=True), args.prime, args.start, args.end,
                     window=args.pause_utc)
    print(json.dumps(summary), flush=True)
    if summary['status'] != 'ok':
        raise SystemExit(1)


if __name__ == '__main__':
    with deadline(int(os.environ.get('REPLAY_TIMEOUT_SECONDS', str(72 * 3600)))):
        main()
