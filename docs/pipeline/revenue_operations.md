# Daily revenue operations

`settle-revenue-daily` runs `python -m settle.revenue.worker` at **03:17 UTC once
per day** (`17 3 * * *`). The existing SBE job remains independent. The cutoff is
the previous completed UTC day; on the first day of a month this is the previous
month's last day. Set the provider credentials, `DATABASE_URL`, deployment code
version and optional `SETTLE_INPUT_REVISION`. The worker requires Postgres and
bootstraps the revenue tables; deploy the input schema (`db/schema.sql`) once.

A first installation calculates yesterday only. Later ticks fill missing dates
since the first known run, capped to the last 90 completed UTC days. Explicit
backfills use `--from YYYY-MM-DD --to YYYY-MM-DD` (both required, at most 90 days
back), optionally `--prime obex`. Older monthly figures stay canonical. A code or
input revision change recomputes yesterday; restating earlier dates is explicit.

A session advisory lock per prime prevents overlap, including different revisions
and manual backfills. A killed process releases its lock; the next lock holder
marks leftover running attempts abandoned. The ledger records each bounded retry
(up to three), and successful publication plus attempt completion commits in one
transaction. Failed days do not prevent other dates/primes being attempted. API
readers keep the last committed success. An identical revision is reused.

The worker emits structured per-prime reports and exits nonzero on failure, with
`ALERT` log entries containing prime, cutoff and error type (no provider secrets).
Use Railway's failed-job notification/monitoring integration for those signals.
The revenue API status endpoint in step 7 will also report missing daily
completion, including a scheduler that never fired. Do not interpret a healthy
web process as proof of fresh daily results. Alert delivery destinations are
managed in the deployment's monitoring configuration, not hard-coded here.
