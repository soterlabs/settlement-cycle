# Railway deployment — `settlement-cycle-data`

The project is defined in code: **`.railway/railway.ts`** (Railway
Infrastructure-as-Code). It declares the existing Postgres plus two services
that deploy from this repo with the same image (`pip install -e ".[api]"`):

| Service | Start command | Runs |
|---|---|---|
| `settle-api`  | `uvicorn settle.api.app:app` | read-only API, `/healthz` health check, public domain |
| `settle-cron` | `python scripts/daily_cron.py --tasks tmf` | daily at 02:30 UTC — extends the SBE history to the finalized head and persists it under a `runs` row |

Variables (values live in Railway, `preserve()`d in the authoring file):
`DATABASE_URL` (reference to the Postgres service), `ENVIO_API_TOKEN`,
`ETH_RPC`, `API_CORS_ORIGINS`, `LOG_LEVEL`. Optional: `HYPERSYNC_REORG_MARGIN`.

```
railway link --project settlement-cycle-data --environment production
railway config plan      # preview
railway config apply     # apply (asks for confirmation)
```

02:30 UTC leaves HyperSync time to index yesterday's last blocks; the cron stops
at the finalized head anyway, so a slow archive only means a slightly earlier
`to_block`, never a partial write. A failed run is recorded as
`runs.status = 'failed'` with the traceback in `runs.error` and exits 1.

There is deliberately **no `railway.json`** in the repo root any more. Railway's
Config-as-Code file sits at the root of every service's source and is not
scoped to one service, so the old placeholder (`NIXPACKS`, `numReplicas: 0`)
could override the authoring file for both services — re-imposing the build
that produced `uvicorn: command not found`, with zero replicas. The authoring
file is the single source of truth.

The GitHub source branch is set in the authoring file; switch it to `main`
once the phase-1 PR is merged.
