"""Publish event-backed cash-flow months. Read-only chain collection, no MSC replay."""

import argparse
import calendar
import gzip
import json
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from ...store.db import connect
from . import collector, store
from .build import build, income_report


def month_dates(start, end):
    cursor = start.replace(day=1)
    result = []
    while cursor <= end:
        last = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
        if start <= last <= end:
            result.append(last)
        cursor = last + timedelta(days=1)
    if end not in result:
        result.append(end)
    return sorted(result, reverse=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", type=date.fromisoformat)
    parser.add_argument(
        "--to",
        dest="end",
        type=date.fromisoformat,
        default=datetime.now(UTC).date() - timedelta(days=1),
    )
    parser.add_argument("--env-file")
    parser.add_argument(
        "--cache-dir", default=os.environ.get("CASH_FLOW_CACHE_DIR", "/tmp/settle-cash-flow")
    )
    parser.add_argument(
        "--evidence-dir", help="Reuse independently collected public evidence files"
    )
    parser.add_argument("--output-dir", help="Write a reviewable public-data preview")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    load_dotenv(args.env_file)
    if args.dry_run:
        os.environ["CASH_FLOW_CAPTURE_PERSIST"] = "0"
    start = args.start or args.end.replace(day=1) - timedelta(days=1)
    if (
        start.year != 2026
        or args.end.year != 2026
        or not start <= args.end < datetime.now(UTC).date()
    ):
        parser.error("Spark 2026 completed UTC days are required")
    code = (
        os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    )
    with connect(autocommit=True) as conn:
        if not args.dry_run:
            store.apply_schema(conn)
            if not conn.execute(
                "SELECT pg_try_advisory_lock(hashtext('reporting-cash-flow-spark-v1'))"
            ).fetchone()[0]:
                raise SystemExit("Another cash-flow publisher is running")
        failed = False
        for cutoff in month_dates(start, args.end):
            try:
                path = Path(args.evidence_dir) / f"{cutoff}.json.gz" if args.evidence_dir else None
                if path and path.exists():
                    with gzip.open(path, "rt") as f:
                        evidence = json.load(f)
                    if evidence["cutoff"] != str(cutoff):
                        raise ValueError("Evidence cutoff differs")
                else:
                    evidence = collector.collect(
                        cutoff,
                        cache_dir=args.cache_dir,
                        progress=lambda ch, n, cutoff=cutoff: print(
                            json.dumps({"cutoff": str(cutoff), "chain": ch, "stage": n}), flush=True
                        ),
                    )
                document, events = build(evidence, income_report(conn, cutoff))
                revision = None if args.dry_run else store.publish(conn, document, events, code)
                if args.output_dir:
                    out = Path(args.output_dir)
                    out.mkdir(parents=True, exist_ok=True)
                    with gzip.open(out / f"{cutoff}.json.gz", "wt") as f:
                        json.dump(
                            {"document": document, "events": events, "revision_id": revision}, f
                        )
                print(
                    json.dumps(
                        {
                            "cutoff": str(cutoff),
                            "revision": revision,
                            "event_count": len(events),
                            "missing_chains": document["coverage"]["missing_chains"],
                            "unclassified_events": document["coverage"]["unclassified_events"],
                        }
                    ),
                    flush=True,
                )
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "cutoff": str(cutoff),
                            "status": "failed",
                            "error_type": type(exc).__name__,
                        }
                    ),
                    flush=True,
                )
                failed = True
        if failed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
