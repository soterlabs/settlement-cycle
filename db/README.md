# Postgres raw-data store

One-time setup. The pipeline runs fine without Postgres — the read-through
cache in `src/settle/extract/cache.py` silently no-ops the PG layer when
`DATABASE_URL` is unset, and the local pickle cache (`~/.cache/msc-settle/`)
remains the primary store.

## 1. Provision Postgres on Railway

```bash
# Once per project:
railway login
railway init                       # link this repo to a Railway project
railway add --plugin postgres      # provision the managed Postgres instance

# Then grab the connection string:
railway variables                  # find DATABASE_URL in the output
```

Copy `DATABASE_URL` into your local `.env` (see `.env.example`).

## 2. Apply the schema

Two files, because they are owned by different pipelines:

```bash
psql "$DATABASE_URL" -f db/schema.sql         # raw_data + the HyperSync log store
psql "$DATABASE_URL" -f db/schema_daily.sql   # runs + the decoded SBE tables
```

Idempotent — re-running is safe (`CREATE TABLE IF NOT EXISTS`).

`schema.sql` is kept separate on purpose: it carries an `ALTER TABLE` on
`hypersync_logs`, which takes an ACCESS EXCLUSIVE lock on a table the monthly
pipeline reads. `settle.store.db.apply_schema` therefore applies only
`schema_daily.sql`, on every cron tick and at API startup, so the hourly job
never locks the monthly pipeline out. See `docs/PRD_daily_pipeline_api.md`.

`schema.sql` alone can also be applied via the sync script:

```bash
PYTHONPATH=src python3 scripts/sync_raw_data.py --apply-schema
```

## One-time backfill from local cache

If you've been running the pipeline locally before wiring up Postgres,
`~/.cache/msc-settle/` holds hundreds of cached fetches. Lift them into
Postgres in one shot:

```bash
PYTHONPATH=src python3 scripts/backfill_cache_to_postgres.py
```

Idempotent (`ON CONFLICT DO NOTHING`). Reads every `*.pkl` under
`$SETTLE_CACHE_DIR` (default `~/.cache/msc-settle/`), unpickles, and
inserts `(source, args_hash, payload)`. The `args` JSONB column for
backfilled rows is a placeholder — see the script's docstring for why
(the pickle filename only stores the hash, not the original args).
Future fetches via the read-through cache populate `args` properly.

## How the data lands

| Layer                                              | Behavior                                                             |
|----------------------------------------------------|----------------------------------------------------------------------|
| Local pickle (`~/.cache/msc-settle/`)              | Fast LRU on top; first read for any `(source, args)` populates here  |
| Postgres (`raw_data`)                              | Durable source of truth; append-only, `ON CONFLICT DO NOTHING`       |
| Upstream (Dune / RPC / Chronicle / Redstone / …)   | Fetched only on full cache miss                                      |

Read order on `@cached`-decorated extract calls: local pickle → Postgres →
upstream. Fresh fetches write to both. Historical rows are never mutated.

## What keeps the store warm

Nothing on a schedule fills `raw_data` on its own — it is a read-through
cache, so it gains rows as a side effect of the pipeline running:

| Writer | When | What it writes |
|---|---|---|
| the monthly runners (`scripts/run_<prime>_2026.py`, `build_sky_total_2026.py`) | when a settlement is generated | whatever `(source, args_hash)` keys that run touches |
| `settle-cron` on Railway (`scripts/cron.py`) | hourly | the HyperSync log store, plus the decoded SBE tables in `schema_daily.sql` |
| `scripts/sync_raw_data.py` | manually | warms the cache for a config/venue change without waiting for a settlement run |

A `Sync raw data to Postgres` GitHub Action used to do the third of these on
pushes to `main`. It was disabled in 2026-05 pending the initial backfill and
removed in 2026-09: the backfill was long done, and by then `settle-cron` and
the runners covered the same ground — a push-triggered re-run of the extract
pipeline would only have been a second, uncoordinated writer against the same
Dune and RPC quota. It is in git history if it is ever wanted back.

Every writer is idempotent: if no new `(source, args_hash)` keys appear, every
cell hits the cache and the run is a fast no-op.
