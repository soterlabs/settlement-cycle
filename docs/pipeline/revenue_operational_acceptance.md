# Daily revenue operational acceptance

Feature implementation and production acceptance are separate. PRs #207–#212
provide the pipeline, persistence, daily schedule, read API and required-input
failure gate. This follow-up closes the remaining measurement and monitoring work.

## Live measurements

Run the verifier against a separate Postgres database, with provider credentials
and `db/schema.sql` applied. Use a pinned checkout and an empty local cache for
each subprocess. Baseline all six primes through one completed date, repeat
that date, then advance the entire fleet by one day. Required-Postgres mode
never promotes local-only files; tests have isolated local caches.

A completely empty baseline is valid but expensive on a throttled provider.
For the September 14→15 acceptance run, seed only immutable production snapshots
whose pinned block is at or below that chain's September 14 closing pin. Date
resolutions must also target no later than September 14 UTC close; cached block
timestamps must be no later than that close. Exclude all legacy/unpinned entries.
The initial attempt started without event rows/ranges. To avoid repeating an
expensive cold-history download, the final run also seeds previously absent
event streams from production. Every seeded interval is clipped to block
25,979,136, the **minimum** of the eight chains' September 14 closing pins;
reject any selected stream with a post-cutoff timestamp below that ceiling.
Copy all saved rows through the ceiling before claiming intervals, in one
local transaction. Higher-block history is fetched normally. Preserve inputs
already obtained in the interrupted baseline; none advances the cutoff.

The selection manifest records cutoff, per-chain ceilings, counts and hashes.
This seeds only past inputs without prewarming September 15. The first complete
run is a seeded baseline, not a cold-start cost benchmark or an independent
validation of the source cache. Warm reuse and next-day demand remain measurable. Never measure
incremental demand against an unrestricted production cache that already
contains future inputs. Keep generated reports and seeded databases outside Git.

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

### Production publication and unattended scheduling — September 16, 2026

PR #213 merged as `03fa6c64e61adbd835102297e6a174e93973b6e0` after three review
rounds and passing unit/Postgres CI. All six primes have published results
through September 15. Grove caught up after the required official SOFR
observation became available.

The unmodified Railway schedule (`17 20 * * *`) triggered without a manual
restart. The first durable attempt started at 20:21:52 UTC; all six finished
successfully by 20:24:05 UTC, approximately 132 seconds later. Scheduling was
about five minutes later than the configured minute. The completed deployment
was `52f02c95-76e3-4db2-836e-d51ef1279881`, running the merged commit above.
The API returned HTTP 200 with all six primes current and successful.

Every field of each newly published September 15 calculation exactly matched
its previous `8071f5b` calculation. This verifies reuse across the deployed code
change; it is not a measurement of a newly advanced cutoff.

The independent completion checker passed in
[GitHub Actions run 35143823615](https://github.com/soterlabs/settlement-cycle/actions/runs/35143823615).
That monitor invocation was manually dispatched; its own 03:00 UTC schedule
has not yet been observed. The Railway worker evidence above is automatic.

### Notification delivery

Receipt is still unverified. Railway returned no notification rules for this
project/workspace, and the current credentials cannot read delivery history.
No deliberate test notification has been sent. A destination and authorization
to send a test there are pending; a workflow failure alone does not establish
that someone receives it.

Dashboard integration uses [the existing handoff prompt](msc_dashboard_daily_revenue_prompt.md).
