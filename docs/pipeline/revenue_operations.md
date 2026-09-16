# Daily revenue operations

`settle-revenue-daily` runs `python -m settle.revenue.worker` at **20:17 UTC once
per day** (`17 20 * * *`). The existing SBE job remains independent. The cutoff is
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

Set `REVENUE_START_DATE` once at deployment to the first intended cutoff. This
recovers missed initial ticks even if no attempt could reach Postgres. It stays
fixed across redeployments; the rolling 90-day floor still bounds catch-up.
Without it, an empty installation starts yesterday. Local publication refuses
uncommitted code/config changes; Railway supplies the deployment commit.

`REVENUE_TIMEOUT_SECONDS` defaults to 21600 (six hours). A hard watchdog exits 124
on expiry, closing connections and releasing locks; the next tick recovers the
abandoned attempt. Set this deployment environment variable before starting the
process (the watchdog starts before dotenv loading). Failed/expired jobs emit
an alert signal and never publish an uncommitted result.

The previous reference-rate blocker is addressed by the [official SOFR input
gate](sofr_inputs.md). Schedule after the Fed's revision window; allow catch-up
when weekend/holiday observations have not yet published. The calendar currently
covers 2026–2027 and must be maintained from the official full-close schedule.
