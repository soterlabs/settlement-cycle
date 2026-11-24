# Railway automation: daily catch-up, manual replays, monthly yields

Everything below runs on Railway (`settlement-cycle-data`, production), built
from `main`. Nothing needs a local machine.

| Service | Schedule (UTC) | Does |
|---|---|---|
| `settle-revenue-<prime>` / `settle-revenue-daily` (Grove) | 20:17 daily | Publishes yesterday's daily revenue and fills unpublished gaps (existing) |
| `settle-revenue-catchup` | 23:00 daily | Retries failed/missing cutoffs of the last 14 days, then runs queued manual replays |
| `settle-allocation-yields` | 06:30 daily | Computes, verifies and publishes monthly allocation yields once a month's close is on `main` |

New months need no change: the daily worker starts a new month-to-date on the
1st, and the MSC dashboard derives its daily months from the calendar.

## Daily catch-up (`scripts/revenue_catchup.py`)

For each prime, a completed day of the last `CATCHUP_DAYS` (14) is retried when
it has no published revision, or when its latest attempt is `failed` or
`abandoned`. Primes run smallest first, each with a 2-hour budget for starting
new cutoffs (`CATCHUP_PRIME_BUDGET_SECONDS`); the rest wait for the next night.
A day whose latest attempt is still `running` is left to its owner,
and each day is re-checked while holding the prime's lock, so a day the 20:17
worker published in the meantime is skipped (a `running` attempt seen while
holding the lock is stale and is retried). Retries go through
`settle.revenue.replay`: one cutoff per prime lock, newest first, never started
16:00–21:00 UTC, with the worker's reference-rate deferral, bounded attempts and
immutable revisions. A day that was published normally is **never** recomputed
automatically.

    python scripts/revenue_catchup.py --dry-run     # what tonight's run would retry

## Manual replays (stale days after a code/config change)

Re-running published days stays a deliberate decision. Queue it; the next
catch-up run executes it on Railway:

    python scripts/request_revenue_replay.py --prime spark --from 2026-11-01 --to 2026-11-10 \
        --reason "S73 added to Spark config"
    python scripts/request_revenue_replay.py --list
    python scripts/request_revenue_replay.py --cancel <id>      # a running request stops before its next cutoff

The request is a row in `revenue_replay_requests`. Progress (`next_cutoff`) is
recorded after every cutoff, so a request cut off by the job's 15-hour deadline
resumes the next night. The command only needs `DATABASE_URL` (for example
`railway run --service settle-revenue-catchup python scripts/request_revenue_replay.py ...`).

## Monthly allocation yields (`scripts/allocation_yields_job.py`)

For Osero, Obex, Grove and Spark (Keel and Skybase have no allocations) and
every completed month from `YIELDS_FIRST_MONTH`:

1. **Already published** → nothing (no automatic revisions).
2. **Close not on `main`** → `waiting_for_close`. The control is
   `reconciliation/monthly-allocation-controls/<prime>/<month>/provenance.json`;
   its sha256 must equal `output_sha256["settlements/<prime>/<month>/provenance.json"]`
   in `reconciliation/<month>/finalization.json`. A mismatch fails the prime.
   **The month-close PR must include these control copies** (settlements
   `provenance.json` files are git-ignored).
3. Capture → idle deductions → readiness → yields → preview, the same scripts
   and flags as the manual publications. Capture checkpoints live on the
   `allocation-yields-work` volume (`/work`), so a restarted run resumes.
4. **Independent check** (`scripts/verify_allocation_preview.py`): every gross,
   retained, net, monthly return and APY is recomputed from the control and the
   document; any problem, or a control venue without a row, blocks publication.
   The document must also be byte-reproducible from the stored artifacts.
5. **Publish** the revision into `allocation_monthly_results` and, in the same
   transaction, the inputs and outputs (history, idle deductions, pins,
   readiness, yields, preview, verification) into `allocation_monthly_artifacts`
   (`db/schema_allocation_artifacts.sql`). The month's work directory is then
   removed.

Unavailable costs or yields (for example Spark rows waiting on the Savings
split) are published as null, never as zero. One run handles one prime at a
time, smallest first; a second concurrent run exits (`already_running`).

Check a published month without writing anything:

    python scripts/allocation_yields_job.py --prime obex --month 2026-10 --check-published --work-dir /tmp/yields

Fetch an artifact:

    SELECT name, sha256, length(content) FROM allocation_monthly_artifacts
     WHERE prime='grove' AND month='2026-11-01';
