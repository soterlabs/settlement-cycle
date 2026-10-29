# October 2026 settlement and reporting API backfill

The October backfill upgrades the **latest daily revision** for each prime/date
to the format containing both settlement and reporting views. Historical
revisions and canonical monthly settlements remain immutable.

As of 2026-10-27, the requested window is October 1–26 inclusive: 26 completed
UTC days × six primes = 156 daily snapshots. October 27–31 are populated by the
daily schedule as those days complete. Reference-rate publication gates still
apply; a deferred cutoff is picked up by a later daily run.

## Run and resume

Run this in a dedicated Railway job using the merged image, the same Postgres
store and provider-variable references as `settle-revenue-daily`:

```sh
python -m settle.revenue.worker \
  --from 2026-10-01 --to 2026-10-26 \
  --missing-views-only --newest-first --wait-for-lock \
  --prime keel --prime obex --prime skybase --prime osero --prime spark --prime grove
```

The lightweight primes run first. Recent dates precede older dates, making the
latest API view available while older history is still processing. The job
shares the existing per-prime advisory lock, required-input guard, retries and
atomic publication with the daily worker. If a daily calculation is already
running, the backfill waits for its lock rather than silently skipping that
prime. Manual runs use the actual UTC time for reference-rate eligibility,
including before the evening publication gate. An interrupted process releases its
lock; restart the same command to resume. Extraction inputs already persisted
in Postgres are reused.

`--missing-views-only` differs from `--missing-only`: a date published in the old
format still needs upgrading. Once its latest revision contains both v2 views,
the job skips that date even after a code-version change. If a later legacy
correction supersedes a v2 revision, the date needs upgrading again. Deliberate
recalculation of already-compatible dates uses the same window **without** the
missing-only flags.

The job must identify its actual merged code revision through Railway's commit
metadata, or `REVENUE_CODE_VERSION` when uploading an image without GitHub source
metadata. Set a suitable `REVENUE_TIMEOUT_SECONDS` for the explicit historical
window; the daily worker retains its separate six-hour default. No provider
credentials should appear in commands, logs or this document.

The dedicated backfill service has no public domain and no recurring schedule.
It is uploaded from the merged checkout without a GitHub auto-deploy source, so
unrelated future merges cannot rerun this historical job. Its independent
deadline and start command do not alter the daily worker.

## Ongoing daily publication

The permanent per-prime services remain scheduled at **20:17 UTC every day**.
Each has an independent runtime budget; the existing `settle-revenue-daily`
service runs Grove and five additional services run the other primes:

```sh
python -m settle.revenue.worker --prime PRIME --require-views
```

`--require-views` refuses both reuse and publication of a result without the
settlement and reporting view format. It does not invent missing contractual
inputs: Anchorage accrual remains explicitly unavailable until confirmed terms
are configured. A view-format check is separate from per-venue data completeness.

Keep the existing fixed `REVENUE_START_DATE` and provider configuration. The
worker attempts the latest eligible cutoff first, then recovers unpublished gaps
within the rolling 90-day window. Explicit historical windows retain their order
unless `--newest-first` is supplied. The scheduled
completion monitor remains independent of API process health.

## Verify progress

Use the [dashboard API guide](settlement-reporting-api.md) to query:

```text
/v2/settlement/{prime}/latest
/v2/reporting/{prime}/at/{cutoff}?revision={same_revision_id}
/v2/settlement/{prime}/history?start=2026-10-01&end=2026-10-26
/v2/reporting/{prime}/history?start=2026-10-01&end=2026-10-26
/v1/revenue/status
```

For each prime, both histories should eventually contain all 26 cutoffs with
`availability: available`, matching revision IDs and source-result hashes for
the same date. Missing/incompatible dates are incomplete work, not zero income.
Check `latest_attempt` for running/failed/abandoned work and account for deferred
reference-rate dates. Do not sum MTD snapshots as daily revenue.

The job's progress is durable in `revenue_attempts` and `revenue_results`.
Successful backfill rows may have `result.complete: false` when a configured
venue has explicitly unavailable inputs; retain those reasons in dashboards.
