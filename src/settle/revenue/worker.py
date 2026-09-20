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
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

from psycopg.types.json import Jsonb

from settle.compute import compute_monthly_pnl
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.extract.publication import PublicationGuard, RequiredInputFailure
from settle.store.db import connect

from . import store
from .reference_rates import available_cutoff
from .reference_rates import prepare as prepare_reference_rates
from .verification import PRIMES, ProviderAudit, digest, validate_window

_log = logging.getLogger('settle.revenue.worker')


def planned_dates(published, attempted, today, start=None, end=None, initial=None):
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
        if initial is not None:
            if initial > yesterday:
                raise ValueError('initial cutoff must be a completed UTC day')
            seen.append(max(floor, initial))
        first, last = min(seen, default=yesterday), yesterday
    return [first + timedelta(days=i) for i in range((last - first).days + 1)
            if start is not None or first + timedelta(days=i) not in published
            or first + timedelta(days=i) == yesterday]


def lock_key(prime):
    return int.from_bytes(hashlib.sha256(f'daily-revenue:{prime}'.encode()).digest()[:8],
                          'big', signed=True)


def run_prime(conn, prime, *, today=None, start=None, end=None, compute=compute_monthly_pnl,
              capture=store.capture_versions, attempts=3, pause=time.sleep, prepare=prepare_reference_rates,
              now=None):
    now = now or (datetime.combine(today, datetime.min.time(), UTC).replace(hour=20, minute=17)
                  if today else datetime.now(UTC))
    today = today or now.astimezone(UTC).date()
    if not conn.autocommit or attempts < 1:
        raise ValueError('worker requires an autocommit lock session and positive retry count')
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
        initial = os.environ.get("REVENUE_START_DATE")
        dates = planned_dates(published, attempted, today, start, end,
                              date.fromisoformat(initial) if initial else None)
        config = load_prime_by_id(prime)
        ready_through = available_cutoff(config, today - timedelta(days=1), now)
        # Anchor a first weekend installation at the last publishable day so
        # Monday's gap planner also recovers Friday and Saturday.
        if start is None and dates and ready_through < dates[0] and ready_through not in published:
            dates.insert(0, ready_through)
        for cutoff in dates:
            if cutoff > ready_through:
                results.append({'cutoff': str(cutoff), 'status': 'deferred',
                                'reason': 'reference_rate_not_due'})
                continue
            versions = capture()
            for attempt in range(attempts):
                attempt_id = uuid.uuid4()
                conn.execute('''INSERT INTO revenue_attempts (attempt_id,prime,cutoff,versions,status)
                    VALUES (%s,%s,%s,%s,'running')''',
                    (attempt_id, prime, cutoff, Jsonb(dataclasses.asdict(versions))))
                try:
                    config = load_prime_by_id(prime)
                    prepared = prepare(conn, config, cutoff)
                    if capture() != versions:
                        raise RuntimeError("input/config/code revision changed during preparation")
                    provenance = {'manual_input_revision': versions.inputs,
                                  **(prepared.provenance if prepared else {})}
                    latest = store.read(conn, prime, cutoff=cutoff)
                    # Reuse only the currently published revision. If an official
                    # correction returns to an older value, publish a new revision
                    # rather than leaving the intervening value as "latest".
                    if (latest and latest['code_version'] == versions.code
                            and latest['configuration_version'] == versions.configuration
                            and latest['input_provenance'] == provenance):
                        resolved = dataclasses.replace(versions, inputs=latest['input_revision'])
                        conn.execute("UPDATE revenue_attempts SET status='succeeded', finished_at=NOW(), "
                                     "revision_id=%s, versions=%s WHERE attempt_id=%s",
                                     (latest['revision_id'], Jsonb(dataclasses.asdict(resolved)), attempt_id))
                        results.append({'cutoff': str(cutoff), 'status': 'reused',
                                        'revision_id': latest['revision_id']})
                        break
                    resolved = dataclasses.replace(versions, inputs=digest({
                        'manual_revision': versions.inputs,
                        'reference_rates': prepared.snapshot_id if prepared else None,
                        'supersedes': latest['revision_id'] if latest else None}))
                    conn.execute('UPDATE revenue_attempts SET versions=%s WHERE attempt_id=%s',
                                 (Jsonb(dataclasses.asdict(resolved)), attempt_id))
                    kwargs = {'reference_rate_history': prepared.history} if prepared else {}
                    with PublicationGuard(), ProviderAudit() as audit:
                        pnl = compute(config, Month(cutoff.year, cutoff.month), as_of=cutoff, **kwargs)
                    if pnl.prime_id != prime or pnl.as_of != cutoff:
                        raise ValueError('calculation returned a different prime or cutoff')
                    if audit.dune_attempts:
                        raise RuntimeError('Dune was used during revenue calculation')
                    if capture() != versions:
                        raise RuntimeError('input/config/code revision changed during calculation')
                    # Validate that the lock session still exists before publishing.
                    # A broken connection must fail; never reconnect behind a lost lock.
                    with conn.transaction():
                        revision = store.publish(conn, pnl, resolved,
                                                 input_provenance=provenance)
                        conn.execute("UPDATE revenue_attempts SET status='succeeded', finished_at=NOW(), "
                                     "revision_id=%s WHERE attempt_id=%s", (revision, attempt_id))
                    results.append({'cutoff': str(cutoff), 'status': 'succeeded', 'revision_id': revision})
                    break
                except (Exception, RequiredInputFailure) as exc:
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


@contextmanager
def deadline(seconds):
    """A hard process deadline cannot be swallowed by compute fallback handlers.

    The OS closes DB sessions; uncommitted writes roll back and locks release.
    The next tick reclaims any running attempt left by this interrupted worker.
    """
    if seconds <= 0:
        raise ValueError('deadline must be positive')
    def expired():
        os.write(2, b'ALERT daily revenue worker deadline exceeded\n')
        os._exit(124)
    timer = threading.Timer(seconds, expired)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


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
        except (Exception, RequiredInputFailure) as exc:
            report = {'prime': prime, 'status': 'failed', 'error_type': type(exc).__name__}
            _log.error('ALERT daily revenue worker failed for %s: %s', prime, type(exc).__name__)
        reports.append(report)
        print(json.dumps(report), flush=True)
    if any(r['status'] == 'failed' for r in reports):
        raise SystemExit(1)


if __name__ == '__main__':
    with deadline(int(os.environ.get('REVENUE_TIMEOUT_SECONDS', '21600'))):
        main()
