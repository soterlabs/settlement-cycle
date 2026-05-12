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

```bash
psql "$DATABASE_URL" -f db/schema.sql
```

Idempotent — re-running is safe (`CREATE TABLE IF NOT EXISTS`).

Or apply via the sync script's `--apply-schema` flag:

```bash
PYTHONPATH=src python3 scripts/sync_raw_data.py --apply-schema
```

## 3. (One-time) Configure GitHub Action secrets

The `Sync raw data to Postgres` workflow (`.github/workflows/sync-raw-data.yml`)
needs these repo secrets:

| Secret             | Value                                                   |
|--------------------|---------------------------------------------------------|
| `DATABASE_URL`     | from `railway variables`                                |
| `DUNE_API_KEY`     | Dune API key                                            |
| `ETH_RPC`          | per-chain RPC endpoints (Alchemy / drpc / etc.)         |
| `BASE_RPC`         | "                                                       |
| `ARBITRUM_RPC`     | "                                                       |
| `OPTIMISM_RPC`     | "                                                       |
| `UNICHAIN_RPC`     | "                                                       |
| `AVALANCHE_C_RPC`  | "                                                       |
| `PLUME_RPC`        | "                                                       |

Set via the GitHub UI (Settings → Secrets and variables → Actions) or `gh`:

```bash
gh secret set DATABASE_URL --body "$DATABASE_URL"
# repeat per secret
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

## When the GitHub Action runs

`.github/workflows/sync-raw-data.yml` triggers on `push` to `main` *only*
when files that define what gets fetched change:

- `src/settle/extract/**`  (the fetcher code)
- `src/settle/queries/**`  (Dune SQL templates)
- `config/*.yaml`           (prime configs — venues, oracles)
- `scripts/sync_raw_data.py`
- `db/schema.sql`

Edits outside these paths (docs, tests, compute logic) don't change the
set of raw-data keys, so the workflow skips. Manual runs via
`workflow_dispatch` work too.

The action is idempotent: if no new `(source, args_hash)` keys appear,
every cell hits the cache and the run is a fast no-op.
