#!/usr/bin/env python3
"""Compute, verify and publish monthly allocation yields without a human step.

Scheduled daily on Railway. For every prime with allocations and every
completed month from ``YIELDS_FIRST_MONTH`` on, it does nothing when that month
is already published, waits (status ``waiting_for_close``) until the month's
close is merged to main, and otherwise runs the same pipeline as the manual
October publication:

  capture -> idle deductions -> readiness -> yields -> preview
  -> independent verification -> publish + store artifacts (one transaction)

The close is a precondition, not something this job produces: the revenue
control is ``reconciliation/monthly-allocation-controls/<prime>/<month>/
provenance.json`` and its sha256 must equal the committed
``reconciliation/<month>/finalization.json`` output for that prime.

Publication is refused when the independent check
(scripts/verify_allocation_preview.py) reports any arithmetic problem or a
control venue without a row. Unavailable values stay null; nothing is
defaulted to zero. The capture checkpoints in ``--work-dir`` (a Railway
volume), so a restarted job resumes; the work directory of a month is removed
only after its artifacts are stored with the publication.

    python scripts/allocation_yields_job.py                       # scheduled run
    python scripts/allocation_yields_job.py --prime obex --month 2026-10 --check-published
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from verify_allocation_preview import passes, verify  # noqa: E402

_log = logging.getLogger("settle.allocation_yields_job")
# Primes whose allocations have a capital pipeline; Keel and Skybase have none.
# Small primes first, so a long Spark capture never delays them.
PRIMES = ("osero", "obex", "grove", "spark")
DEFAULT_FIRST_MONTH = "2026-11"
LOCK_KEY = int.from_bytes(hashlib.sha256(b"allocation-yields-job").digest()[:8], "big", signed=True)
CONTROLS = Path("reconciliation/monthly-allocation-controls")


def months(first, today):
    """Completed calendar months from ``first`` (YYYY-MM) through last month."""
    y, m = map(int, first.split("-"))
    out = []
    while date(y, m, 1) < date(today.year, today.month, 1):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_end(month):
    y, m = map(int, month.split("-"))
    return (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def close_control(root, prime, month):
    """(control path, None) when the committed close covers this prime, else (None, reason)."""
    control = root / CONTROLS / prime / month / "provenance.json"
    finalization = root / "reconciliation" / month / "finalization.json"
    if not finalization.exists() or not control.exists():
        return None, "waiting_for_close"
    expected = json.loads(finalization.read_text()).get("output_sha256", {}).get(
        f"settlements/{prime}/{month}/provenance.json")
    if expected is None:
        return None, "waiting_for_close"
    if sha256(control) != expected:
        raise ValueError(f"{control} does not match the committed close for {prime} {month}")
    return control, None


def run_step(name, args, *, root, attempts=1, pause=time.sleep, run=subprocess.run):
    for attempt in range(1, attempts + 1):
        _log.info("step %s attempt %s", name, attempt)
        if run([sys.executable, "-u", *map(str, args)], cwd=root).returncode == 0:
            return
        if attempt < attempts:
            pause(60)
    raise RuntimeError(f"step {name} failed")


def pipeline(root, prime, month, control, work, *, run_step=run_step):
    inputs = work / "inputs"
    rel_control = control.relative_to(root)
    start, end = f"{month}-01", month_end(month).isoformat()
    run_step("capture", ["scripts/validate_allocation_capital.py", prime, "--start", start, "--end", end,
                         "--capture-only", "--block-resolver", "hypersync", "--provenance", rel_control,
                         "--output-dir", inputs], root=root, attempts=3)
    run_step("idle", ["scripts/validate_allocation_financing.py", "--provenance", rel_control,
                      "--history-dir", inputs, "--capture-idle-only", "--block-resolver", "hypersync"],
             root=root, attempts=3)
    run_step("readiness", ["scripts/report_allocation_capital_readiness.py", "--prime", prime, "--month", month,
                           "--history", inputs / "history.jsonl.gz",
                           "--idle-deductions", inputs / "idle_deductions.json",
                           "--settlements-root", CONTROLS, "--apply-capital-adapters",
                           "--output", work / "readiness.json.gz", "--markdown", work / "readiness.md"], root=root)
    run_step("yields", ["scripts/report_allocation_yields.py", "--month", month, "--settlements-root", CONTROLS,
                        "--readiness", work / "readiness.json.gz", "--output", work / "yields.json",
                        "--markdown", work / "yields.md"], root=root)
    run_step("preview", ["scripts/publish_monthly_allocations.py", "--readiness", work / "readiness.json.gz",
                         "--yields", work / "yields.json", "--output", work / "preview.json"], root=root)


def _publisher(root):
    spec = importlib.util.spec_from_file_location("publish_monthly_allocations",
                                                  root / "scripts/publish_monthly_allocations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def checked_document(root, prime, month, control, work):
    """The single verified document for (prime, month), or raise."""
    documents = _publisher(root).prepare(work / "readiness.json.gz", work / "yields.json")
    if json.dumps(documents, indent=2) + "\n" != (work / "preview.json").read_text():
        raise ValueError("publication document is not reproducible from the reviewed artifacts")
    if len(documents) != 1 or documents[0]["prime"] != prime or documents[0]["month"] != month:
        raise ValueError("preview must hold exactly this prime and month")
    report = verify(documents[0], json.loads(control.read_text()))
    (work / "verification.json").write_text(json.dumps(report, indent=1) + "\n")
    if not passes(report):
        raise ValueError(f"independent verification failed: {report['problems'][:5]} "
                         f"missing={report['control_venues_without_row']}")
    return documents[0], report


ARTIFACTS = ("inputs/history.jsonl.gz", "inputs/idle_deductions.json", "inputs/pins.json",
             "readiness.json.gz", "readiness.md", "yields.json", "yields.md", "preview.json", "verification.json")


def store(conn, document, work, code_version):
    """Publish the document and its artifacts atomically; return the revision."""
    from settle.revenue.allocations import apply_schema, publish
    with conn.transaction():
        apply_schema(conn)
        conn.execute((ROOT / "db/schema_allocation_artifacts.sql").read_text())
        revision = publish(conn, document)
        for name in ARTIFACTS:
            path = work / name
            if not path.exists():
                raise ValueError(f"missing artifact {name}")
            content = path.read_bytes()
            conn.execute(
                """INSERT INTO allocation_monthly_artifacts
                       (revision_id, name, sha256, prime, month, code_version, content)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (revision, name, hashlib.sha256(content).hexdigest(), document["prime"],
                 f"{document['month']}-01", code_version, content))
    return revision


def compare(published, document):
    """Differences between the published and recomputed allocation rows."""
    keep = lambda d: {r["venue_id"]: r for r in d["allocations"]}  # noqa: E731
    old, new = keep(published), keep(document)
    return sorted(v for v in old.keys() | new.keys() if old.get(v) != new.get(v))


def process(root, prime, month, work_root, *, connect, code_version, check_published=False,
            pipeline=pipeline, read=None):
    from settle.revenue.allocations import read as read_published
    read = read or read_published
    with connect() as conn:
        published = read(conn, prime, month)
    work = work_root / prime / month
    if published is not None and not check_published:
        shutil.rmtree(work, ignore_errors=True)  # left behind if a run died right after publishing
        return {"prime": prime, "month": month, "status": "already_published",
                "revision_id": published["revision_id"]}
    control, reason = close_control(root, prime, month)
    if control is None:
        return {"prime": prime, "month": month, "status": reason}
    # A revised close (new control) must not resume a capture made for the old one.
    marker = work / "control.sha256"
    if work.exists() and (not marker.exists() or marker.read_text() != sha256(control)):
        _log.info("resetting work directory for %s %s: control changed", prime, month)
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    marker.write_text(sha256(control))
    # On failure the work directory is kept: capture checkpoints resume next run.
    pipeline(root, prime, month, control, work)
    document, report = checked_document(root, prime, month, control, work)
    summary = {k: report[k] for k in ("rows", "rows_with_cost", "rows_with_net_apy", "unallocated_difference")}
    if check_published:
        shutil.rmtree(work)
        if published is None:
            return {"prime": prime, "month": month, "status": "not_published", **summary}
        diff = compare(published, document)
        return {"prime": prime, "month": month, "status": "differs" if diff else "matches_published",
                "venues_differing": diff, **summary}
    with connect() as conn:
        revision = store(conn, document, work, code_version)
    shutil.rmtree(work)
    return {"prime": prime, "month": month, "status": "published", "revision_id": revision, **summary}


@contextlib.contextmanager
def single_instance(connect):
    with connect() as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
            yield False
            return
        try:
            yield True
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def main():
    from dotenv import load_dotenv

    from settle.revenue.store import capture_versions
    from settle.revenue.worker import deadline
    from settle.store.db import connect

    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--prime", choices=PRIMES, action="append")
    p.add_argument("--month", action="append", help="YYYY-MM (default: every completed month from "
                                                    "YIELDS_FIRST_MONTH)")
    p.add_argument("--work-dir", type=Path, default=Path(os.environ.get("YIELDS_WORK_DIR", "/work")))
    p.add_argument("--check-published", action="store_true",
                   help="Recompute already-published months and compare; never publishes")
    args = p.parse_args()
    os.environ["SETTLE_REQUIRE_POSTGRES"] = "1"
    logging.basicConfig(level=logging.INFO)
    code_version = capture_versions().code
    _log.info("allocation yields job code version %s", code_version)
    plan = [(prime, month) for month in (args.month or months(
                os.environ.get("YIELDS_FIRST_MONTH", DEFAULT_FIRST_MONTH), datetime.now(UTC).date()))
            for prime in (args.prime or PRIMES)]
    reports = []
    work_root = args.work_dir / "allocation-yields"
    with deadline(int(os.environ.get("YIELDS_TIMEOUT_SECONDS", str(23 * 3600)))), \
            single_instance(lambda: connect(autocommit=True)) as acquired:
        if not acquired:
            print(json.dumps({"status": "already_running"}), flush=True)
            return
        for prime, month in plan:
            try:
                report = process(ROOT, prime, month, work_root, connect=connect, code_version=code_version,
                                 check_published=args.check_published)
            except Exception as exc:  # one prime's failure must not stop the others
                _log.error("ALERT allocation yields failed prime=%s month=%s error=%s: %s",
                           prime, month, type(exc).__name__, exc)
                report = {"prime": prime, "month": month, "status": "failed", "error_type": type(exc).__name__}
            reports.append(report)
            print(json.dumps(report), flush=True)
    if any(r["status"] in {"failed", "differs"} for r in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
