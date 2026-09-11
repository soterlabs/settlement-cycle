#!/usr/bin/env python
"""settle-cron — the pipeline's scheduled entry point (docs/PRD_daily_pipeline_api.md).

Phase 1: extend the Smart Burn Engine history to the latest finalized block
and persist the decoded rows (``sbe_kicks``, ``sky_burns``,
``sbe_param_changes``) under a new ``runs`` row. Idempotent: a re-run inserts
nothing new and still records an ok run — which is also how a gap left by any
earlier failure heals, since every run re-offers the whole history.

Runs **hourly** (see the PRD's cadence note). The floor on freshness is not
the schedule but the extractor's finalized-head bound: HyperSync's archive
head minus the reorg margin is ~100 minutes behind wall clock, so hourly puts
the data at most ~2.7 h old where daily left it up to ~25 h.

Exit codes: 0 ok (including a deliberate skip) · 1 the run failed (recorded as
status='failed') · 2 missing configuration.

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python3 scripts/cron.py [--to-block N] [--tasks tmf]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any

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

_log = logging.getLogger("settle.cron")

# A run that is still ``running`` and younger than this blocks a new one. At an
# hourly cadence an overlap should be impossible (a run takes ~12 s, and the
# store's statement timeout bounds a stuck one at 5 min), but a scheduler that
# fires while the previous execution is wedged would otherwise interleave two
# writers over the same tables. A crashed run cannot block forever: past this
# window it is treated as abandoned.
_OVERLAP_WINDOW_MIN = 50


def _settle_version() -> str:
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GITHUB_SHA")
    return f"{__version__}+{sha[:12]}" if sha else __version__


def _blocking_run(conn: Any, kind: str, window_min: int) -> tuple[int, Any] | None:
    """A still-``running`` run of ``kind`` started within ``window_min``, if any."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_id, started_at FROM runs
            WHERE kind = %s AND status = 'running'
              AND started_at > NOW() - make_interval(mins => %s)
            ORDER BY started_at DESC LIMIT 1
            """,
            (kind, window_min),
        )
        row = cur.fetchone()
    return (int(row[0]), row[1]) if row else None


def task_tmf(to_block: int | None) -> int:
    """SBE history -> store. Returns 0 (ok) / 1 (recorded failure).

    Shape matters here:

    * everything, including ``connect`` / ``apply_schema`` / ``start_run``, is
      inside the try — a setup failure must still exit 1 rather than escape as
      an unhandled traceback with no ``runs`` row;
    * the extraction runs with **no** database connection open. It takes
      minutes, and an idle session behind Railway's Postgres proxy is exactly
      the half-open-socket case the store's keepalives exist for;
    * the three upserts, the pin update and ``finish_run`` share one
      transaction, so a run that dies midway leaves no durable rows for a later
      document to over-report against;
    * a failure is recorded on a **fresh** connection, because the one that
      failed may itself be the reason.
    """
    cfg = yaml.safe_load((_REPO / "config" / "tmf.yaml").read_text())
    # Fingerprint everything the run reads that can change what it extracts:
    # `contracts` decides WHICH events are fetched, `history` the range and the
    # classification. Hashing only one of them would let a Splitter migration
    # produce a different dataset under an identical config_hash.
    fingerprint = {"history": cfg.get("history"), "contracts": cfg.get("contracts")}
    run_id: int | None = None
    try:
        with connect() as conn:
            apply_schema(conn)
            blocking = _blocking_run(conn, "tmf_history", _OVERLAP_WINDOW_MIN)
            if blocking is not None:
                _log.warning(
                    "tmf_history run %d has been running since %s — skipping this tick "
                    "rather than interleaving two writers", blocking[0], blocking[1],
                )
                return 0
            run_id = runs_store.start_run(
                conn, "tmf_history", settle_version=_settle_version(),
                config_hash=runs_store.config_hash(fingerprint),
            )
        _log.info("tmf_history run %d started", run_id)

        ds, bound = build_dataset(cfg, to_block=to_block)

        with connect() as conn:
            n_k = tmf_store.upsert_kicks(conn, ds.kicks, run_id)
            n_b = tmf_store.upsert_burns(conn, ds.burns, run_id)
            n_p = tmf_store.upsert_param_changes(conn, ds.param_changes, run_id)
            months = aggregate(ds.kicks, ds.burns, "monthly")
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE runs SET pin_block = %s, pin_ts = to_timestamp(%s) WHERE run_id = %s",
                    (ds.to_block, ds.to_ts, run_id),
                )
            # Commits the whole unit of work.
            runs_store.finish_run(conn, run_id, {
                "from_block": ds.from_block, "to_block": ds.to_block, "to_ts": ds.to_ts,
                "bound": bound, "kicks_total": len(ds.kicks), "burns_total": len(ds.burns),
                "param_changes_total": len(ds.param_changes),
                "new_kicks": n_k, "new_burns": n_b, "new_param_changes": n_p,
                "latest_month": months[-1].as_json() if months else None,
            })
        _log.info(
            "tmf_history run %d ok - to_block %d (%s); %d kicks (%d new), %d burns (%d new)",
            run_id, ds.to_block, bound, len(ds.kicks), n_k, len(ds.burns), n_b,
        )
        return 0
    except Exception as exc:
        _log.exception("tmf_history run %s FAILED", run_id if run_id is not None else "(not started)")
        if run_id is not None:
            try:
                with connect() as conn:
                    runs_store.fail_run(
                        conn, run_id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                    )
            except Exception:
                _log.exception("could not record the failure on run %d", run_id)
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
