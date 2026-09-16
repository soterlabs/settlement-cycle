# Daily revenue operational acceptance

Feature implementation and production acceptance are separate. PRs #207–#212
provide the pipeline, persistence, daily schedule, read API and required-input
failure gate. This follow-up closes the remaining measurement and monitoring work.

## Live measurements

Run the verifier against a separate, initially empty Postgres database, with
provider credentials and `db/schema.sql` applied. Use a pinned checkout and an
empty local cache for each subprocess. Baseline all six primes through one
completed date, repeat that date, then advance the entire fleet by one day.
Do not measure incremental demand against a production cache that already
contains the next day's inputs. Keep generated reports outside Git.

The verifier uses the same official reference-rate preparation as publication.
Reference refreshes are counted separately from historical extraction; changed
SOFR snapshots invalidate numerical comparison. Exact equality covers every
calculation field. On a same-date rerun, only finalized-head/boundary checks and
the explicit official reference refresh are permitted upstream reads.

`verification.json` records requests by provider and RPC method, response bytes,
cache hits, extraction time and remaining calculation time. These are HTTP
attempts (including retries), not provider billing credits. The baseline may
need older raw events for opening state; published cutoffs remain within 90 days.
Initial cache population is not representative of recurring daily cost.

## Independent completion monitor

The GitHub Actions `Daily revenue completion` workflow checks the read API daily
at 03:00 UTC, separately from Railway's scheduler. The worker starts at 20:17 UTC,
has a six-hour hard deadline, and the monitor allows 30 minutes of grace. A check
at 03:00 on September 17 therefore requires the September 15 cutoff produced by
the September 16 job. The API's calendar-day freshness flag alone would signal a
false alarm after midnight, before the next scheduled run was due.

Missing/stale cutoffs, failed or abandoned current attempts, overdue running
attempts, malformed responses and an unavailable API fail the workflow. A newer
run can still be in progress if the previously due result was published. Neither
the workflow nor the checker starts calculations or calls chain providers.

Keep the monitor's schedule/deadline constants aligned with the Railway worker.
Workflow failure is an alert signal, not proof of notification receipt. Verify
notification routing and an actual delivery separately; do not infer it from a
successful HTTP check or a Railway deployment status. Railway project crash
notifications cover failures; the independent monitor also detects missed runs.

## Acceptance record

Live fleet results, measured demand, unattended scheduling evidence and alert
receipt will be recorded here after verification. Dashboard integration uses
[the existing handoff prompt](msc_dashboard_daily_revenue_prompt.md).
