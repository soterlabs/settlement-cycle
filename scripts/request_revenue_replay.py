#!/usr/bin/env python3
"""Queue, list or cancel a manual replay of published daily revenue.

Nothing is computed here: the request is a row in ``revenue_replay_requests``
that the nightly ``settle-revenue-catchup`` job on Railway executes (23:00 UTC,
newest cutoff first, one cutoff per prime lock, never in 16:00-21:00 UTC).
Use it after a venue or configuration change makes published days stale.

    python scripts/request_revenue_replay.py --prime spark --from 2026-11-01 --to 2026-11-10 \\
        --reason "S73 added to Spark config"
    python scripts/request_revenue_replay.py --list
    python scripts/request_revenue_replay.py --cancel 7
"""

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "db/schema_revenue_replay_requests.sql"


def validate(prime, start, end, today, primes):
    if prime not in primes:
        raise ValueError(f"unknown prime {prime}")
    if start > end:
        raise ValueError("--from must not be after --to")
    if end >= today:
        raise ValueError("replay only completed UTC days")


def main():
    from dotenv import load_dotenv

    from settle.revenue.verification import PRIMES
    from settle.store.db import connect

    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--prime", choices=PRIMES)
    action.add_argument("--list", action="store_true")
    action.add_argument("--cancel", type=int, metavar="REQUEST_ID")
    p.add_argument("--from", dest="start", type=date.fromisoformat)
    p.add_argument("--to", dest="end", type=date.fromisoformat)
    p.add_argument("--reason")
    args = p.parse_args()
    with connect(autocommit=True) as conn:
        conn.execute(SCHEMA.read_text())
        if args.list:
            for row in conn.execute(
                    "SELECT request_id, prime, first_cutoff, last_cutoff, status, next_cutoff, "
                    "jsonb_array_length(failed), requested_at, reason FROM revenue_replay_requests "
                    "ORDER BY request_id DESC LIMIT 20"):
                print(json.dumps(dict(zip(("id", "prime", "from", "to", "status", "next", "failed",
                                           "requested_at", "reason"), map(str, row), strict=True))))
            return
        if args.cancel is not None:
            row = conn.execute("UPDATE revenue_replay_requests SET status='cancelled', finished_at=NOW() "
                               "WHERE request_id=%s AND status='pending' RETURNING request_id",
                               (args.cancel,)).fetchone()
            print(json.dumps({"cancelled": bool(row)}))
            if not row:
                raise SystemExit("only a pending request can be cancelled")
            return
        if not (args.start and args.end and args.reason):
            p.error("--prime needs --from, --to and --reason")
        validate(args.prime, args.start, args.end, datetime.now(UTC).date(), PRIMES)
        request_id = conn.execute(
            "INSERT INTO revenue_replay_requests (prime, first_cutoff, last_cutoff, reason) "
            "VALUES (%s, %s, %s, %s) RETURNING request_id",
            (args.prime, args.start, args.end, args.reason)).fetchone()[0]
        print(json.dumps({"request_id": request_id, "status": "pending",
                          "runs": "next settle-revenue-catchup run (23:00 UTC)"}))


if __name__ == "__main__":
    main()
