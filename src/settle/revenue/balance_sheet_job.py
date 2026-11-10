"""Collect closing stocks at month ends and latest completed UTC day; no daily P&L."""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from dotenv import load_dotenv

from ..store.db import connect
from .balance_sheet_sources import collect
from .balance_sheet_store import DDL, publish
from .spark_reporting_job import month_dates


def refresh(conn, cutoff, code, progress=None):
    row = conn.execute(
        "SELECT document->'closing_pins' FROM reporting_spark_components WHERE cutoff=%s ORDER BY publication_order DESC LIMIT 1",
        (cutoff,),
    ).fetchone()
    document = collect(cutoff, candidates=row[0] if row else {}, progress=progress)
    revision = publish(conn, document, code)
    return revision, document


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument(
        "--to",
        dest="end",
        type=date.fromisoformat,
        default=datetime.now(UTC).date() - timedelta(days=1),
    )
    parser.add_argument("--output-dir")
    parser.add_argument("--workers", type=int, choices=[1, 2, 3], default=1)
    args = parser.parse_args()
    if (
        not date(2026, 1, 1) <= args.start <= args.end < datetime.now(UTC).date()
        or args.end.year != 2026
    ):
        parser.error("Completed Spark 2026 dates required")
    import subprocess

    code = (
        os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    )
    with connect(autocommit=True) as conn:
        if not conn.execute(
            "SELECT pg_try_advisory_lock(hashtext('spark-closing-stocks-v1'))"
        ).fetchone()[0]:
            raise SystemExit("Closing publisher already running")
        conn.execute(DDL)

        def run(cutoff):
            with connect(autocommit=True) as worker_conn:
                return process(worker_conn, cutoff)

        def process(worker_conn, cutoff):
            revision, doc = refresh(
                worker_conn,
                cutoff,
                code,
                progress=lambda item, cutoff=cutoff: print(
                    json.dumps({"cutoff": str(cutoff), "stage": item}), flush=True
                ),
            )
            if args.output_dir:
                import gzip
                from pathlib import Path

                p = Path(args.output_dir)
                p.mkdir(exist_ok=True, parents=True)
                with gzip.open(p / (str(cutoff) + ".json.gz"), "wt") as f:
                    json.dump(doc, f)
            print(
                json.dumps(
                    {
                        "cutoff": str(cutoff),
                        "revision": revision,
                        "status": "partial" if doc["errors"] else "succeeded",
                        "errors": doc["errors"],
                    }
                ),
                flush=True,
            )

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(run, reversed(month_dates(args.start, args.end))))


if __name__ == "__main__":
    main()
