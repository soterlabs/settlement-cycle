#!/usr/bin/env python3
"""Retry daily revenue cutoffs that are missing or whose latest attempt failed.

Scheduled on Railway at 23:00 UTC, after the 20:17 per-prime workers. For each
prime it looks back ``--days`` (default 14) completed UTC days and replays,
newest first, only the cutoffs that

  * have no published revision at all, or
  * whose most recent attempt ended ``failed`` or ``abandoned``,

and skips a cutoff whose most recent attempt is still ``running`` (the 20:17
job, or a manual replay, owns it). Each cutoff is re-checked just before its
retry, so one the daily job publishes in the meantime is not recomputed.

A cutoff whose current revision was published normally is never recomputed:
re-running stale days after a code/config change stays a manual decision (use
the ``settle-revenue-replay`` service). Each retry goes through
:func:`settle.revenue.replay.replay`, so it takes the prime lock per cutoff,
never starts between 16:00 and 21:00 UTC, keeps the worker's reference-rate
deferral, bounded retries and immutable revisions, and deletes nothing.

It then executes manual replay requests queued with
scripts/request_revenue_replay.py (table ``revenue_replay_requests``), one
cutoff at a time through the same driver, recording progress so a request
interrupted by the job deadline resumes the next night.

    python scripts/revenue_catchup.py [--prime spark] [--days 14] [--dry-run]
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import logging
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

_log = logging.getLogger("settle.revenue.catchup")
RETRY_STATUSES = frozenset({"failed", "abandoned"})
LOCK_KEY = int.from_bytes(hashlib.sha256(b"daily-revenue-catchup").digest()[:8], "big", signed=True)
SCHEMA = Path(__file__).resolve().parents[1] / "db/schema_revenue_replay_requests.sql"


def targets(published, latest_attempt, today, days):
    """Cutoffs to retry, newest first.

    ``published``: cutoffs with at least one revision; ``latest_attempt``:
    cutoff -> status of its most recent attempt.
    """
    if days < 1:
        raise ValueError("look-back must be at least one day")
    window = [today - timedelta(days=i) for i in range(1, days + 1)]
    return [d for d in window if latest_attempt.get(d) != "running"
            and (d not in published or latest_attempt.get(d) in RETRY_STATUSES)]


def load_state(conn, prime, first, last):
    published = {r[0] for r in conn.execute(
        "SELECT DISTINCT cutoff FROM revenue_results WHERE prime=%s AND cutoff BETWEEN %s AND %s",
        (prime, first, last))}
    latest = dict(conn.execute(
        "SELECT DISTINCT ON (cutoff) cutoff, status FROM revenue_attempts "
        "WHERE prime=%s AND cutoff BETWEEN %s AND %s ORDER BY cutoff, started_at DESC",
        (prime, first, last)).fetchall())
    return published, latest


def guarded_run(conn, prime, *, start, end, run=None, **kwargs):
    """Run one cutoff only if it is still a catch-up target, checked UNDER the prime lock.

    Advisory locks are re-entrant per session: take the prime's lock first,
    re-read the cutoff's state, and only then let the worker take it again.
    A day the 20:17 worker published while we waited is therefore skipped.
    """
    from settle.revenue.worker import lock_key
    from settle.revenue.worker import run_prime as worker_run

    run = run or worker_run
    if start != end:
        raise ValueError("catch-up replays one cutoff at a time")
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (lock_key(prime),)).fetchone()[0]:
        return {"prime": prime, "status": "already_running", "results": []}
    try:
        published, latest = load_state(conn, prime, start, start)
        if not targets(published, latest, start + timedelta(days=1), 1):
            return {"prime": prime, "status": "ok", "results": [{"cutoff": str(start), "status": "skipped"}]}
        return run(conn, prime, start=start, end=end, **kwargs)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key(prime),))


def catch_up(connect, primes, today, days, *, replay, dry_run=False, worker=None):
    reports = []
    for prime in primes:
        with connect() as conn:
            published, latest = load_state(conn, prime, today - timedelta(days=days), today - timedelta(days=1))
        todo = targets(published, latest, today, days)
        _log.info("catch-up prime=%s cutoffs=%s", prime, [str(d) for d in todo])
        if dry_run or not todo:
            reports.append({"prime": prime, "status": "ok", "planned": [str(d) for d in todo]})
            continue
        failed, skipped = [], []
        for cutoff in todo:
            summary = replay(connect, prime, cutoff, cutoff, run=functools.partial(guarded_run, run=worker))
            failed += summary["failed"]
            if summary["counts"].get("skipped"):
                skipped.append(str(cutoff))  # published or taken over since planning
        reports.append({"prime": prime, "status": "failed" if failed else "ok",
                        "planned": [str(d) for d in todo], "skipped": skipped, "failed": failed})
    return reports


def run_requests(connect, *, replay):
    """Execute pending (or interrupted) manual replay requests, oldest first."""
    from psycopg.types.json import Jsonb

    with connect() as conn:
        conn.execute(SCHEMA.read_text())
        requests = conn.execute(
            "SELECT request_id, prime, first_cutoff, last_cutoff, next_cutoff, failed FROM revenue_replay_requests "
            "WHERE status IN ('pending', 'running') ORDER BY request_id").fetchall()
    reports = []
    for request_id, prime, first, last, next_cutoff, failed in requests:
        _log.info("replay request id=%s prime=%s from=%s to=%s next=%s", request_id, prime, first, last, next_cutoff)
        cutoff = last if next_cutoff is None else next_cutoff
        with connect() as conn:
            claimed = conn.execute(
                "UPDATE revenue_replay_requests SET status='running' WHERE request_id=%s "
                "AND status IN ('pending', 'running') RETURNING request_id", (request_id,)).fetchone()
        if not claimed:
            continue  # cancelled since the queue was read
        cancelled = False
        while cutoff >= first:
            with connect() as conn:
                state = conn.execute("SELECT status FROM revenue_replay_requests WHERE request_id=%s",
                                     (request_id,)).fetchone()
            if state is None or state[0] != "running":
                cancelled = True
                break
            try:
                failed = [*failed, *replay(connect, prime, cutoff, cutoff)["failed"]]
            except ValueError as exc:  # e.g. a cutoff that is not a completed UTC day
                failed = [*failed, {"cutoff": str(cutoff), "status": "failed", "error_type": type(exc).__name__}]
            cutoff -= timedelta(days=1)
            with connect() as conn:
                conn.execute("UPDATE revenue_replay_requests SET next_cutoff=%s, failed=%s WHERE request_id=%s",
                             (cutoff, Jsonb(failed), request_id))
        if cancelled:
            reports.append({"request_id": request_id, "prime": prime, "status": "cancelled", "failed": failed})
            continue
        status = "failed" if failed else "done"
        with connect() as conn:
            conn.execute("UPDATE revenue_replay_requests SET status=%s, finished_at=NOW() WHERE request_id=%s "
                         "AND status='running'", (status, request_id))
        reports.append({"request_id": request_id, "prime": prime, "status": "failed" if failed else "ok",
                        "failed": failed})
    return reports


@contextmanager
def single_instance(connect):
    with connect() as conn:
        acquired = conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def main():
    from dotenv import load_dotenv

    from settle.revenue.replay import replay
    from settle.revenue.store import capture_versions
    from settle.revenue.verification import PRIMES
    from settle.revenue.worker import deadline
    from settle.store.db import connect

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--prime", choices=PRIMES, action="append")
    parser.add_argument("--days", type=int, default=int(os.environ.get("CATCHUP_DAYS", "14")))
    parser.add_argument("--dry-run", action="store_true", help="List the cutoffs that would be retried")
    args = parser.parse_args()
    os.environ["SETTLE_REQUIRE_POSTGRES"] = "1"
    logging.basicConfig(level=logging.INFO)
    if not args.dry_run:
        _log.info("catch-up code version %s", capture_versions().code)  # fail fast, as the replay does
    connect_ = lambda: connect(autocommit=True)  # noqa: E731
    with deadline(int(os.environ.get("CATCHUP_TIMEOUT_SECONDS", str(15 * 3600)))), \
            single_instance(connect_) as acquired:
        if not acquired:
            print(json.dumps({"status": "already_running"}), flush=True)
            return
        reports = catch_up(connect_, args.prime or PRIMES, datetime.now(UTC).date(), args.days,
                           replay=replay, dry_run=args.dry_run)
        if not args.dry_run and not args.prime:
            reports += run_requests(connect_, replay=replay)
    for report in reports:
        print(json.dumps(report), flush=True)
    if any(r["status"] != "ok" for r in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
