# September 2026 API revenue backfill

Backfill all six primes (Grove, Spark, OBEX, Keel, Skybase and Osero) from
September 1, 2026 at **00:00:00 UTC**. Each API cutoff is a completed UTC day;
September 1 covers the first day and September 22 covers the first 22 days.
Opening balances are pinned at the last block before September 1. Closing pins
are the last block at or before the selected day's UTC close. The existing
finality and successor-block checks apply to both boundaries.

These are **daily month-to-date estimates**, not individual-day revenue amounts.
Do not add the snapshots together. No incomplete September 23 snapshot is
published by this backfill. Monthly settlement artifacts remain unchanged.
Capital tracing work in PRs #215/#216 is not a dependency of this backfill.

## Execution

Use the production daily worker's provider credentials and the same Postgres
store read by the API. Keep credentials out of source and command-line arguments.
The existing worker fetches missing input data from archival RPC and HyperSync,
reuses persisted source data, and rejects any Dune use during the calculation.
Official reference rates and all required-input publication guards still apply.

Commit code before starting; the worker verifies its code/config/input versions
before publishing each result. Run from that unchanged checkout:

```sh
python -m settle.revenue.worker \
  --from 2026-09-01 --to 2026-09-22 --missing-only
```

The initial production inspection on September 23 found September 14–21 for each
prime: 48 published prime/date pairs. This run fills 84 missing pairs: September
1–13 plus September 22, for each of six primes. It preserves those existing 48
selections across code revisions. Per-prime locks also protect against the normal
scheduled worker. If another worker owns a prime, retry after it completes.
Each successful cutoff becomes visible through the existing API immediately;
failed days are not represented as zero revenue. The worker logs progress per
cutoff and retains attempts and successful results in Postgres.

Set the deployed `REVENUE_START_DATE` to `2026-09-01` so scheduled catch-up also
covers this intended start date after interruptions. It remains an installation
start date, not a value that advances each month. The existing 90-day lookback
limit continues to apply. Explicit `--missing-only` reruns fill any remaining
gaps without restating already published snapshots.

## External API verification

For each prime, request:

```text
/v1/revenue/spark/history?start=2026-09-01&end=2026-09-22&limit=90
/v1/revenue/spark/at/2026-09-01
```

Replace `spark` with the other five prime IDs. Completion requires 22 distinct
cutoffs per prime (132 total), with September 1 included, and unchanged revision
IDs/result hashes for the original September 14–21 selections. Check the result
period and opening/closing pins on the first-day snapshot. The API labels these
as provisional month-to-date estimates; backfilling does not make them canonical
monthly settlements. A successful worker launch alone is not completion.
