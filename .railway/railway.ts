import { defineRailway, github, postgres, preserve, project, service, volume } from "railway/iac";

/**
 * settlement-cycle-data — Railway project (Infrastructure as Code).
 *
 * Postgres is the store shared by the monthly pipeline's cache layer and the
 * daily pipeline (docs/PRD_daily_pipeline_api.md). Two services deploy from
 * this repo with the same image (`pip install -e ".[api]"`):
 *
 *   settle-api   read-only FastAPI (settle.api.app), /healthz health check
 *   settle-cron  scripts/daily_cron.py, daily 02:30 UTC — extends the SBE
 *                history to the finalized head and persists it under a run
 *
 * Secrets are `preserve()`d: values live in Railway, never in source.
 * `railway config plan` previews, `railway config apply` applies.
 */
export default defineRailway(() => {
  // Switch to "main" once the phase-1 PR is merged.
  const settlementCycle = github("soterlabs/settlement-cycle", { branch: "feat/daily-api-phase1", checkSuites: false });

  const Postgres = postgres("Postgres", { region: "us-west2" });
  Postgres.networking = { privateNetworkEndpoint: "postgres", tcpProxies: { "5432": {} } };
  const postgresVolume = volume("postgres-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "us-west2", sizeMB: 50000 });

  // One explicit image for both services (see Dockerfile) — Nixpacks put the
  // `api` extra outside its runtime venv.
  const build = { builder: "DOCKERFILE" as const, dockerfilePath: "Dockerfile" };

  const settleApi = service("settle-api", {
    source: settlementCycle,
    replicas: { "us-west2": 1 },
    build,
    deploy: {
      startCommand: "python -m uvicorn settle.api.app:app --host 0.0.0.0 --port $PORT",
      healthcheckPath: "/healthz",
      healthcheckTimeout: 120,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 5,
    },
    env: { API_CORS_ORIGINS: preserve(), DATABASE_URL: preserve(), LOG_LEVEL: preserve() },
  });

  const settleCron = service("settle-cron", {
    source: settlementCycle,
    replicas: { "us-west2": 1 },
    build,
    deploy: {
      startCommand: "python scripts/daily_cron.py --tasks tmf",
      cronSchedule: "30 2 * * *",
      restartPolicyType: "NEVER",
    },
    env: { DATABASE_URL: preserve(), ENVIO_API_TOKEN: preserve(), ETH_RPC: preserve(), LOG_LEVEL: preserve() },
  });

  return project("settlement-cycle-data", {
    resources: [settleApi, Postgres, settleCron, postgresVolume],
  });
});
