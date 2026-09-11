#!/usr/bin/env python
"""settle-cron — the daily pipeline entry point (docs/PRD_daily_pipeline_api.md).

Phase 1: extend the Smart Burn Engine history to the latest finalized block
and persist the decoded rows (``sbe_kicks``, ``sky_burns``,
``sbe_param_changes``) under a new ``runs`` row. Idempotent: re-running the
same day inserts nothing new and still records an ok run.

Exit codes: 0 ok · 1 the run failed (recorded as status='failed') ·
2 missing configuration.

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python3 scripts/daily_cron.py [--to-block N] [--tasks tmf]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "scripts"))

from build_tmf_history import build_dataset  # noqa: E402

from settle import __version__  # noqa: E402
from settle.compute.tmf_history import aggregate  # noqa: E402
from settle.store import runs as runs_store  # noqa: E402
from settle.store import tmf as tmf_store  # noqa: E402
from settle.store.db import apply_schema, connect  # noqa: E402

_log = logging.getLogger("daily_cron")


def _settle_version() -> str:
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GITHUB_SHA")
    return f"{__version__}+{sha[:12]}" if sha else __version__


def task_tmf(to_block: int | None) -> int:
    """SBE history → store. Returns 0/1."""
    cfg = yaml.safe_load((_REPO / "config" / "tmf.yaml").read_text())
    with connect() as conn:
        apply_schema(conn)
        run_id = runs_store.start_run(
            conn, "tmf_history", settle_version=_settle_version(),
            config_hash=runs_store.config_hash(cfg.get("history")),
        )
        _log.info("tmf_history run %d started", run_id)
        try:
            ds, bound = build_dataset(cfg, to_block=to_block)
            n_k = tmf_store.upsert_kicks(conn, ds.kicks, run_id)
            n_b = tmf_store.upsert_burns(conn, ds.burns, run_id)
            n_p = tmf_store.upsert_param_changes(conn, ds.param_changes, run_id)
            months = aggregate(ds.kicks, ds.burns, "monthly")
            latest = months[-1].as_json() if months else None
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE runs SET pin_block = %s, pin_ts = to_timestamp(%s) WHERE run_id = %s",
                    (ds.to_block, ds.to_ts, run_id),
                )
            runs_store.finish_run(conn, run_id, {
                "from_block": ds.from_block, "to_block": ds.to_block, "to_ts": ds.to_ts,
                "bound": bound, "kicks_total": len(ds.kicks), "burns_total": len(ds.burns),
                "param_changes_total": len(ds.param_changes),
                "new_kicks": n_k, "new_burns": n_b, "new_param_changes": n_p,
                "latest_month": latest,
            })
            _log.info(
                "tmf_history run %d ok — to_block %d (%s); %d kicks (%d new), %d burns (%d new)",
                run_id, ds.to_block, bound, len(ds.kicks), n_k, len(ds.burns), n_b,
            )
            return 0
        except Exception as exc:
            conn.rollback()
            runs_store.fail_run(conn, run_id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            _log.exception("tmf_history run %d FAILED", run_id)
            return 1


TASKS = {"tmf": task_tmf}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tasks", default="tmf", help="comma-separated subset of: " + ",".join(TASKS))
    ap.add_argument("--to-block", type=int, default=None, help="pin the upper block (default: finalized head)")
    args = ap.parse_args()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    missing = [v for v in ("DATABASE_URL", "ENVIO_API_TOKEN") if not os.environ.get(v)]
    if missing:
        print("Missing required env vars: " + ", ".join(missing), file=sys.stderr)
        return 2
    wanted = [t.strip() for t in args.tasks.split(",") if t.strip()]
    unknown = [t for t in wanted if t not in TASKS]
    if unknown:
        print(f"unknown task(s): {unknown}; available: {list(TASKS)}", file=sys.stderr)
        return 2
    rc = 0
    for t in wanted:
        rc = max(rc, TASKS[t](args.to_block))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
