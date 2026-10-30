import { defineRailway, github, postgres, preserve, project, service, volume } from "railway/iac";

/**
 * settlement-cycle-data — Railway project (Infrastructure as Code).
 *
 * Postgres is the store shared by the monthly pipeline's cache layer and the
 * daily pipeline (docs/PRD_daily_pipeline_api.md). Two services deploy from
 * this repo with the same image (`pip install -e ".[api]"`):
 *
 *   settle-api   read-only FastAPI (settle.api.app), /healthz health check
 *   settle-cron  scripts/cron.py, hourly at :17 — extends the SBE history to
 *                the finalized head and persists it under a run
 *
 * Secrets are `preserve()`d: values live in Railway, never in source.
 * `railway config plan` previews, `railway config apply` applies.
 */
export default defineRailway(() => {
  const settlementCycle = github("soterlabs/settlement-cycle", { branch: "main", checkSuites: false });

  const Postgres = postgres("Postgres", { region: "us-west2" });
  Postgres.networking = { privateNetworkEndpoint: "postgres", tcpProxies: { "5432": {} } };
  const postgresVolume = volume("postgres-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "us-west2", sizeMB: 50000 });

  // One explicit image for both services (see Dockerfile) — Nixpacks put the
  // `api` extra outside its runtime venv.
  const build = { builder: "DOCKERFILE" as const, dockerfilePath: "Dockerfile", buildCommand: 'pip install -e ".[api]"' };

  const settleApi = service("settle-api", {
    source: settlementCycle,
    replicas: { "us-west2": 1 },
    build,
    deploy: {
      // sh -c: with the Dockerfile builder the command is not shell-expanded, so $PORT needs a shell.
      startCommand: 'sh -c "python -m uvicorn settle.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"',
      healthcheckPath: "/healthz",
      healthcheckTimeout: 120,
      restartPolicyMaxRetries: 5,
    },
    env: { API_CORS_ORIGINS: preserve(), DATABASE_URL: preserve(), LOG_LEVEL: preserve() },
  });

  const settleCron = service("settle-cron", {
    source: settlementCycle,
    replicas: { "us-west2": 1 },
    build,
    deploy: {
      startCommand: "python scripts/cron.py --tasks tmf",
      // Hourly, off the top of the hour: the engine kicks roughly once an hour,
      // and :00 is where every other Railway cron piles up.
      cronSchedule: "17 * * * *",
      restartPolicyType: "NEVER",
    },
    env: { DATABASE_URL: preserve(), ENVIO_API_TOKEN: preserve(), ETH_RPC: preserve(), LOG_LEVEL: preserve() },
  });

  // Each prime has its own daily process and six-hour watchdog. A slow provider
  // or historical gap for one prime must not consume another prime's budget.
  // Retain this service as Grove's worker and as the provider-secret owner.
  const revenueDaily = service("settle-revenue-daily", {
    source: settlementCycle,
    replicas: { "us-west2": 1 },
    build,
    deploy: {
      startCommand: "python -m settle.revenue.worker --prime grove --require-views",
      cronSchedule: "17 20 * * *",
      restartPolicyType: "NEVER", // bounded retries are recorded by the worker
    },
    env: {
      DATABASE_URL: "${{Postgres.DATABASE_URL}}",
      ENVIO_API_TOKEN: preserve(), ETH_RPC: preserve(), BASE_RPC: preserve(),
      ARBITRUM_RPC: preserve(), OPTIMISM_RPC: preserve(), UNICHAIN_RPC: preserve(),
      AVALANCHE_C_RPC: preserve(), PLUME_RPC: preserve(), MONAD_RPC: preserve(),
      ROBINHOOD_RPC: preserve(), SETTLE_REQUIRE_POSTGRES: "1",
      SETTLE_INPUT_REVISION: "0", REVENUE_START_DATE: preserve(),
    },
  });

  const revenuePrimes = ["spark", "obex", "keel", "skybase", "osero"].map((prime) =>
    service(`settle-revenue-${prime}`, {
      source: settlementCycle,
      replicas: { "us-west2": 1 },
      build,
      deploy: {
        startCommand: `python -m settle.revenue.worker --prime ${prime} --require-views`,
        cronSchedule: "17 20 * * *",
        restartPolicyType: "NEVER",
      },
      // Reference the existing provider settings, without copying credentials
      // into source or creating five independently maintained secret sets.
      env: {
        DATABASE_URL: "${{Postgres.DATABASE_URL}}",
        ...Object.fromEntries([
          "ENVIO_API_TOKEN", "ETH_RPC", "BASE_RPC", "ARBITRUM_RPC", "OPTIMISM_RPC",
          "UNICHAIN_RPC", "AVALANCHE_C_RPC", "PLUME_RPC", "MONAD_RPC", "ROBINHOOD_RPC",
          "SETTLE_INPUT_REVISION", "REVENUE_START_DATE",
        ].map((name) => [name, `\${{settle-revenue-daily.${name}}}`])),
        SETTLE_REQUIRE_POSTGRES: "1",
      },
    })
  );

  // Reads retained revenue results only; catches older backfills/corrections
  // independently of the slower source worker. No provider credentials needed.
  const financialReporting = service("settle-reporting-financial", {
    source: settlementCycle,
    replicas: { "us-west2": 1 },
    build,
    deploy: {
      startCommand: "python -m settle.revenue.financial_backfill --from 2026-01-01",
      cronSchedule: "35 * * * *",
      restartPolicyType: "NEVER", // failed runs are retried by the next hourly tick
    },
    env: { DATABASE_URL: "${{Postgres.DATABASE_URL}}" },
  });

  // Retain the existing uploaded, unscheduled historical jobs. Omitting them
  // from the project would make an otherwise additive rollout delete them.
  const historicalJobs = [
    ["settle-revenue-backfill-oct2026", "--from 2026-10-01 --to 2026-10-26 --missing-views-only --newest-first --wait-for-lock"],
    ["settle-revenue-bootstrap-oct2026", "--from 2026-10-26 --to 2026-10-26 --missing-views-only --wait-for-lock"],
  ].map(([name, window]) => service(name, {
    replicas: { "us-west2": 1 },
    build: { builder: "DOCKERFILE" as const, dockerfilePath: "Dockerfile" },
    deploy: {
      startCommand: `python -m settle.revenue.worker ${window} --prime keel --prime obex --prime skybase --prime osero --prime spark --prime grove`,
      restartPolicyMaxRetries: 5,
    },
    env: Object.fromEntries([
      "DATABASE_URL", "ENVIO_API_TOKEN", "ETH_RPC", "BASE_RPC", "ARBITRUM_RPC",
      "OPTIMISM_RPC", "UNICHAIN_RPC", "AVALANCHE_C_RPC", "PLUME_RPC", "MONAD_RPC",
      "ROBINHOOD_RPC", "SETTLE_INPUT_REVISION", "SETTLE_REQUIRE_POSTGRES",
      "REVENUE_CODE_VERSION", "REVENUE_TIMEOUT_SECONDS",
    ].map((name) => [name, preserve()])),
  }));

  return project("settlement-cycle-data", {
    resources: [settleApi, Postgres, settleCron, revenueDaily, ...revenuePrimes, financialReporting, ...historicalJobs, postgresVolume],
  });
});
