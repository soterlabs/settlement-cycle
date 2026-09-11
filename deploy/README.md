# Railway deployment — `settlement-cycle-data`

Two services deploy from this repo into the project that already hosts the
Postgres (`DATABASE_URL`). Each service points its **Config-as-Code path** at
one of the files here (Service → Settings → Config-as-code):

| Service | Config file | Runs |
|---|---|---|
| `settle-api`  | `deploy/railway.api.json`  | `uvicorn settle.api.app:app` — read-only API, `/healthz` health check |
| `settle-cron` | `deploy/railway.cron.json` | `python scripts/daily_cron.py --tasks tmf`, daily at 02:30 UTC |

Both build with `pip install -e ".[api]"`. Variables (shared via the project's
Postgres reference + shared variables): `DATABASE_URL`, `ENVIO_API_TOKEN`,
`ETH_RPC`, optional `API_CORS_ORIGINS`, `LOG_LEVEL`, `HYPERSYNC_REORG_MARGIN`.

02:30 UTC leaves HyperSync time to index yesterday's last blocks; the cron
stops at the finalized head anyway, so a slow archive only means a slightly
earlier `to_block`, never a partial write.

The root `railway.json` is a build-only placeholder kept for the legacy
sync-raw-data job; it is not used by these services.
