"""Once-daily prime revenue worker; UTC cutoffs, durable retries, no artifact writes.

python -m settle.revenue.worker [--prime obex] [--from YYYY-MM-DD --to YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import UTC, date, datetime, timedelta

from psycopg.types.json import Jsonb

from settle.compute import compute_monthly_pnl
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.store.db import connect

from . import store
from .verification import PRIMES, ProviderAudit, validate_window

_log = logging.getLogger('settle.revenue.worker')


def planned_dates(published, attempted, today, start=None, end=None):
    """Fill known gaps within 90 days; a first installation starts yesterday."""
    floor, yesterday = today - timedelta(days=90), today - timedelta(days=1)
    if (start is None) != (end is None):
        raise ValueError('--from and --to must be supplied together')
    if start is not None:
        validate_window(start, today=today)
        validate_window(end, today=today)
        if start > end:
            raise ValueError('from must not be after to')
        first, last = start, end
    else:
        seen = [d for d in [*published, *attempted] if floor <= d <= yesterday]
        first, last = min(seen, default=yesterday), yesterday
    return [first + timedelta(days=i) for i in range((last - first).days + 1)
            if start is not None or first + timedelta(days=i) not in published
            or first + timedelta(days=i) == yesterday]


def lock_key(prime):
    return int.from_bytes(hashlib.sha256(f'daily-revenue:{prime}'.encode()).digest()[:8],
                          'big', signed=True)


def run_prime(conn, prime, *, today=None, start=None, end=None, compute=compute_monthly_pnl,
              capture=store.capture_versions, attempts=3, pause=time.sleep):
    today = today or datetime.now(UTC).date()
    if prime not in PRIMES:
        raise ValueError('unknown prime')
    if not conn.execute('SELECT pg_try_advisory_lock(%s)', (lock_key(prime),)).fetchone()[0]:
        return {'prime': prime, 'status': 'already_running', 'results': []}
    results = []
    try:
        conn.execute("UPDATE revenue_attempts SET status='abandoned', finished_at=NOW(), "
                     "error_type='WorkerInterrupted' WHERE prime=%s AND status='running'", (prime,))
        published = {r[0] for r in conn.execute('SELECT DISTINCT cutoff FROM revenue_results WHERE prime=%s', (prime,))}
        attempted = {r[0] for r in conn.execute('SELECT DISTINCT cutoff FROM revenue_attempts WHERE prime=%s', (prime,))}
        dates = planned_dates(published, attempted, today, start, end)
        for cutoff in dates:
            versions = capture()
            existing = conn.execute('''SELECT revision_id FROM revenue_results WHERE prime=%s AND cutoff=%s
                AND code_version=%s AND configuration_version=%s AND input_revision=%s LIMIT 1''',
                (prime, cutoff, versions.code, versions.configuration, versions.inputs)).fetchone()
            if existing:
                results.append({'cutoff': str(cutoff), 'status': 'reused', 'revision_id': existing[0]})
                continue
            for attempt in range(attempts):
                attempt_id = uuid.uuid4()
                conn.execute('''INSERT INTO revenue_attempts (attempt_id,prime,cutoff,versions,status)
                    VALUES (%s,%s,%s,%s,'running')''',
                    (attempt_id, prime, cutoff, Jsonb(dataclasses.asdict(versions))))
                try:
                    with ProviderAudit() as audit:
                        pnl = compute(load_prime_by_id(prime), Month(cutoff.year, cutoff.month), as_of=cutoff)
                    if audit.dune_attempts:
                        raise RuntimeError('Dune was used during revenue calculation')
                    if capture() != versions:
                        raise RuntimeError('input/config/code revision changed during calculation')
                    # Validate that the lock session still exists before publishing.
                    # A broken connection must fail; never reconnect behind a lost lock.
                    with conn.transaction():
                        revision = store.publish(conn, pnl, versions)
                        conn.execute("UPDATE revenue_attempts SET status='succeeded', finished_at=NOW(), "
                                     "revision_id=%s WHERE attempt_id=%s", (revision, attempt_id))
                    results.append({'cutoff': str(cutoff), 'status': 'succeeded', 'revision_id': revision})
                    break
                except Exception as exc:
                    # Keep provider URLs/credentials out of the public run ledger.
                    conn.execute("UPDATE revenue_attempts SET status='failed', finished_at=NOW(), "
                                 "error_type=%s WHERE attempt_id=%s", (type(exc).__name__, attempt_id))
                    _log.error('ALERT revenue failure prime=%s cutoff=%s attempt=%s error=%s',
                               prime, cutoff, attempt + 1, type(exc).__name__)
                    if attempt + 1 == attempts or isinstance(exc, ValueError):
                        results.append({'cutoff': str(cutoff), 'status': 'failed', 'error_type': type(exc).__name__})
                        break
                    pause(min(60, 10 * (attempt + 1)))
        return {'prime': prime, 'status': 'failed' if any(r['status'] == 'failed' for r in results) else 'ok',
                'results': results}
    finally:
        if not conn.closed:
            conn.execute('SELECT pg_advisory_unlock(%s)', (lock_key(prime),))


def main():
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prime', choices=PRIMES, action='append')
    parser.add_argument('--from', dest='start', type=date.fromisoformat)
    parser.add_argument('--to', dest='end', type=date.fromisoformat)
    args = parser.parse_args()
    today = datetime.now(UTC).date()
    planned_dates(set(), set(), today, args.start, args.end)  # validate before DB/provider work
    os.environ['SETTLE_REQUIRE_POSTGRES'] = '1'
    logging.basicConfig(level=logging.INFO)
    reports = []
    for prime in args.prime or PRIMES:
        try:
            with connect(autocommit=True) as conn:
                store.apply_schema(conn)
                report = run_prime(conn, prime, today=today, start=args.start, end=args.end)
        except Exception as exc:
            report = {'prime': prime, 'status': 'failed', 'error_type': type(exc).__name__}
            _log.error('ALERT daily revenue worker failed for %s: %s', prime, type(exc).__name__)
        reports.append(report)
        print(json.dumps(report), flush=True)
    if any(r['status'] == 'failed' for r in reports):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
