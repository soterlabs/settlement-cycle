# Daily publication isolation and recovery

## Observed delay, October 29, 2026

Before the evening run, annual reporting exposed October 27 for Grove,
October 25 for Spark, and October 26 for Obex, Keel, Skybase and Osero, against
an October 28 expected cutoff. These are publication dates, not evidence that
the dashboard discarded newer data. Grove's latest raw and financial revisions
both stopped October 27.

The production command was `python -m settle.revenue.worker --require-views`:
Grove, Spark, Obex, Keel, Skybase and Osero ran sequentially under one six-hour
process deadline. Each prime also processed historical gaps before the current
day. A slow prime or old gap could consume the runtime available to every
subsequent prime. Redeployment could interrupt that same sequence.

Live logs from the October 29 run show:

| UTC time | Event |
| --- | --- |
| 20:20 | Grove starts October 28 |
| 20:37 | Grove publishes October 28; Spark starts October 26 |
| 22:11 | Spark publishes October 26; starts October 27 |
| 22:37 | Spark publishes October 27; starts October 28 |

At that check, the other four primes had no attempts since October 27. This
confirms queue delay; it does not establish an RPC outage or that this particular
run exceeded its deadline. Grove had already caught up without intervention.

## Change

Keep `settle-revenue-daily` for Grove and provider-secret ownership. Add
`settle-revenue-spark`, `settle-revenue-obex`, `settle-revenue-keel`,
`settle-revenue-skybase` and `settle-revenue-osero`. All run once daily at 20:17
UTC, from `main`, with `--prime PRIME --require-views`. Each has its own six-hour
watchdog. The new jobs reference the original service's provider settings and
fixed `REVENUE_START_DATE`; they share Postgres but have separate prime locks.

Scheduled runs attempt the most recent eligible date before older gaps.
Reference-rate gates, required-input failures, retries, immutable revisions,
settlement/reporting calculations and the 03:00 completion monitor are retained.
Explicit backfills keep their requested chronological ordering unless
`--newest-first` is provided.

The configuration also restores the financial publisher's intended GitHub
source, so future reporting fixes reach its hourly job. Existing uploaded,
unscheduled historical services are retained without attaching a GitHub source
or rerunning their historical commands.

## Rollout and bounded recovery

1. Merge the PR and wait for its image to build. A merge alone does not change
   Railway service commands or create services.
2. Run `railway config plan --out /tmp/daily-publication-plan.json` against
   production. Expect five new daily services, Grove's command narrowed to one
   prime, and the financial publisher's GitHub source/build configuration restored.
   Expect no service/database/volume deletions or credential changes.
3. Apply that reviewed plan with
   `railway config apply --plan /tmp/daily-publication-plan.json --yes`.
   Verify each command, schedule and resolved provider references. Existing
   per-prime advisory locks prevent old/new workers from publishing concurrently;
   an overlapping job reports `already_running` and must be retried after the
   previous holder exits. A skipped launch is not proof of completion.
   Recovery jobs using `--wait-for-lock` poll without a blocking database query,
   so the normal five-minute query timeout cannot cancel a legitimate wait.
   Their hard process deadline includes time spent waiting.
4. Inspect the current status and each prime's history before launching recovery.
   Normal jobs recover unpublished dates inside their rolling 90-day window.
   For missing October settlement/reporting views, launch **one job per prime**
   from the merged image, using the same Postgres/provider settings:

   ```sh
   python -m settle.revenue.worker --prime PRIME \
     --from 2026-10-01 --to 2026-10-28 \
     --missing-views-only --newest-first --wait-for-lock
   ```

   This window covers completed days at the time of the incident. For a later
   rollout, choose the last completed UTC day explicitly. Reference-rate gates
   can defer dates further. Do not launch six primes sequentially in one recovery
   process. The command preserves already-compatible publications, including the
   last day, and resumes safely after interruption. Increase the recovery job's
   deadline if necessary; keep the daily jobs' six-hour limit.
5. The hourly financial publisher projects new retained raw revisions without
   RPC reads. If raw data is ahead of financial reporting, run a bounded
   `python -m settle.revenue.financial_backfill --prime PRIME --from 2026-10-01 --to 2026-10-28`
   from the merged image to catch up immediately.

## Acceptance

- `/v1/revenue/status` shows the latest eligible cutoff for every prime and no
  failed/abandoned current attempt. The monitor uses the scheduled completion
  deadline, not merely the calendar-day expectation in that endpoint.
- Settlement and reporting histories have matching source revision IDs for each
  eligible October date. Record missing, deferred or unavailable entries rather
  than treating them as zeroes. Do not sum MTD snapshots as daily revenue.
- `/v3/reporting/statements?prime=PRIME&year=2026` selects the new financial
  cutoff when available; raw and financial dates agree. Saved selections and
  previously published monthly summaries remain readable and unchanged.
- The independent `Daily revenue completion` workflow passes after the next
  eligible deadline. API health alone is insufficient evidence of freshness.

The fix does not manufacture missing provider data, revise contractual inputs,
or replace partial venue metrics with zero. Additional concurrent prime jobs
increase simultaneous provider requests; inspect their failure ledgers during
rollout. Each prime retains bounded retries and independent completion status.

## Stale October/November observations (November 23, 2026)

The daily history covered every cutoff from October 1 to November 22 for all six
primes. Many observations had been computed by workers that predated the October
close review (#275), so they lacked allocations that current code includes. The
dashboard's daily view only catalogues venues that appear in an observation, so
those October month-to-date figures silently understated revenue:

| Prime | Stale cutoffs | Missing or incorrect on October 31 (settled October revenue) |
| --- | --- | --- |
| Grove | Oct 1 – Nov 5 | E43 Steakhouse USDC Base ($1,298,008), E44 Diamond PAU LP ($4,146) |
| Osero | Oct 1 – Nov 10 | O3 Gauntlet USDC Prime ($10,607) |
| Spark | Oct 1 – Nov 8 | S70 and S71 absent through Nov 1, and S72 through Nov 8. S64 present at $0. S32 MTD is −$109.57m on Oct 29 and −$109.47m on Oct 31: the 100m-share sUSDS transfer to the SubProxy was booked as a loss before #275's holder-net capital method (settled S32: +$1,983,181) |

Obex, Keel and Skybase observations already include every current venue.

`--missing-views-only` keeps these observations because their views exist. An
explicit worker window would replay them, but it holds the prime lock for the
whole window, so the 20:17 job would report `already_running` and skip a day.
`python -m settle.revenue.replay` instead runs the unchanged worker for one
cutoff at a time, newest first:
- It releases the lock between cutoffs.
- It starts a cutoff only when the lock is free and the time is outside 16:00–03:00
  UTC, re-checking between lock polls. A worst-case Spark cutoff (three attempts
  of about 45 minutes) still finishes before the 20:17 job, which does not wait
  for the lock.
- One failing cutoff is recorded and the next one still runs.
- It keeps reference-rate gates, retries, immutable revisions, reuse of identical
  current revisions and the financial-reporting projection.
- It exits immediately if no code version can be resolved.

While a replay runs, `/v1/revenue/status` `latest_attempt` can show an old
replayed cutoff. The completion monitor ignores attempts older than the due
cutoff, so a replay cannot raise a false alarm. However, a replay attempt that
starts after a failed daily attempt hides that failure from the monitor's
latest-attempt check. In that window, check the services' logs and
`actual_cutoff`, which the monitor still enforces.

Run one unscheduled Railway service per prime (`settle-revenue-replay-<prime>-oct2026`
in `.railway/railway.ts`):

1. Merge, then `railway config plan` and review it: it should add three services
   and delete or alter nothing. Then `railway config apply`.
2. Set `REVENUE_CODE_VERSION` on each service to the merged main commit.
3. Upload from a clean checkout of exactly that commit:
   `railway up --service settle-revenue-replay-<prime>-oct2026 --detach`.
   The services have no GitHub source, so a later push does not restart a replay
   under a different code version.

Progress is visible in each service's logs and in `revenue_attempts`. Verify the
results through `/v1/revenue/<prime>/history` (current venues present, Spark S32
corrected) rather than from the launch alone. Replayed revisions are also
projected to the financial reporting API by the worker's existing hook.
