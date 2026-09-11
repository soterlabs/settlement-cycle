# PRD — Daily data pipeline + read API

*Status: Phase 1 in progress (2026-09-10). Owner: Soter Labs / MSC operator.*

## 1. Problem

Everything this repo produces is monthly and lands in git: a settlement
report per prime per month, the consolidated Sky Net Revenue, the TMF report,
and since #196 a Smart Burn Engine (SBE) history dataset. The dashboard
(`soterlabs/msc-dashboard`) consumes those by cloning `settlement-reports`,
parsing files into committed JSON and rebuilding. That is the right model for
**settled** figures — a data change is a reviewed diff, a build never fetches.

Two needs do not fit it:

1. **Daily freshness** for the Buybacks & Burn tab: the SBE kicks every ~40
   minutes and the burn lands on spell day; a monthly commit cadence shows a
   stale month for most of the month.
2. **A daily MSC run**: month-to-date revenue per prime, every day, persisted
   so the operator (and later the primes) can watch the cycle form instead of
   discovering it at month-end.

Both want a **store that is queried, not a file that is committed** — and a
read API in front of it, because a second consumer (forum post generator, BA
Labs, a public endpoint) is expected.

## 2. Goals

- One daily job, on Railway, that extends the SBE history and (later) runs
  the MSC pipeline month-to-date, writing to the existing Postgres.
- A read-only HTTP API serving the same JSON documents the repo already
  publishes, plus drill-downs, from Postgres.
- Every persisted number is **reproducible and versioned**: which run, which
  pin block, which `settle_version`, which config. Restatements never
  overwrite; the API serves "latest" by default and any run on request.
- Settled monthly reports stay canonical and stay in git. The API labels
  anything sub-monthly as a preview.

## 3. Non-goals

- Replacing the monthly settlement reports or the settlement-reports mirror.
- Writes through the API. It is read-only; the cron is the only writer.
- Real-time (block-level) freshness. The extractor deliberately stops at the
  finalized head (archive head − reorg margin, ~100 min).
- Rebuilding the dashboard's settled tabs (SSR, DR, sky_total) onto the API.
  Those keep the committed-JSON path; only live tabs move.

## 4. Architecture

```
Railway project: settlement-cycle-data
  ┌──────────────┐    writes     ┌──────────────┐    reads     ┌───────────────┐
  │ settle-cron  │ ───────────►  │  Postgres    │ ◄──────────  │  settle-api   │
  │ daily 02:00  │               │ (existing)   │              │  FastAPI, RO  │
  └──────────────┘               └──────────────┘              └──────┬────────┘
     scripts/daily_cron.py         raw_data, hypersync_logs            │ HTTPS
     (same image as the API,       + runs, sbe_kicks, sky_burns        ▼
      different start command)     + (phase 2) daily_revenue …   msc-dashboard
                                                                 live tabs: fetch + ISR
                                                                 settled tabs: committed JSON
```

Both services deploy from this repo. `pip install -e .[api]` is the image;
the cron's start command is `python scripts/daily_cron.py`, the API's is
`uvicorn settle.api.app:app`. Postgres is the one already used by the cache
layer (`DATABASE_URL`); the new tables sit next to `raw_data` /
`hypersync_logs` and self-bootstrap (`CREATE TABLE IF NOT EXISTS`) like they do.

### 4.1 Versioning model (all phases)

```
runs(run_id, kind, prime, month, pin_block, pin_ts, settle_version,
     config_hash, started_at, finished_at, status, error)
```

Every fact table carries `run_id`. A run never updates rows of another run.
"Latest" = the most recent `status='ok'` run for the key `(kind, prime,
month)`. The API exposes `run_id` on every payload and accepts
`?run_id=` to pin. This is what makes a methodology restatement visible as
two runs instead of a silent rewrite of history.

For the SBE history (phase 1) the facts are on-chain events, immutable by
nature, so the tables are keyed by `(block_number, log_index)` with
`ON CONFLICT DO NOTHING`; `run_id` records which run first saw the row.

### 4.2 Two-tier rule for the dashboard

| Tier | Source | Freshness | Examples |
|---|---|---|---|
| settled | committed JSON from settlement-reports, offline build | monthly, reviewed PR | SSR, DR, sky_total |
| live | `settle-api`, server-side fetch with hourly revalidation, fallback to last committed snapshot | daily | Buybacks & Burn, (phase 2) daily revenue |

The dashboard README must state this so neither tier is "fixed" into the other.

### 4.3 API conventions

- Base path `/v1`. Breaking payload changes bump the path; additive ones
  bump `schema_version` inside the document.
- Read-only, JSON, `Cache-Control: public, max-age=300`, ETag on documents.
- CORS: allow the dashboard origins.
- Auth: none for `/v1/tmf/*` (the data is public in settlement-reports).
  Phase 2 previews are gated by an `X-Api-Key` header, one key per consumer,
  keys in Railway variables.
- `GET /healthz` → `{status, db, latest_run}`; used by Railway health checks.

## 5. Phase 1 — SBE history through the pipe (this PR)

**Deliverable:** the Buybacks & Burn tab reads daily data from the API.

| Piece | What |
|---|---|
| `db/schema.sql` | `runs`, `sbe_kicks`, `sky_burns` |
| `settle.store.tmf` | idempotent writers (decoded kicks / burns / parameter changes) + readers |
| `scripts/daily_cron.py` | `build_tmf_history` → persist → also refresh `settlements/tmf/data/` in the working dir (no commit) |
| `settle.api` | FastAPI app: `/v1/tmf/history`, `/v1/tmf/kicks`, `/v1/tmf/burns`, `/v1/tmf/parameter-changes`, `/v1/runs`, `/healthz` |
| Railway | `settle-cron` (cron schedule) + `settle-api` (web) services in `settlement-cycle-data`, `DATABASE_URL` / `ENVIO_API_TOKEN` / `ETH_RPC` shared |
| Dashboard | `loadTmf()` fetches `/v1/tmf/history` with revalidation; falls back to `data/generated/tmf.json` (separate PR in msc-dashboard) |

`/v1/tmf/history` returns **exactly** the `sbe_history.json` document
(`schema_version` 1.1.0) built from the tables via
`compute.tmf_history.build_history_dataset`, so the tab's loader is the only
thing that changes. The committed `settlements/tmf/data/` snapshot is kept as
the settled fallback and refreshed at each MSC, not daily.

Acceptance: cron runs green two days in a row; `curl /v1/tmf/history` equals
the committed snapshot on the same `to_block`; dashboard dev shows yesterday's
kicks.

## 6. Phase 2 — Daily month-to-date MSC run

**Deliverable:** per prime, per day, the month-to-date settlement primitives
and the daily increments, queryable.

Design decisions to take first (open questions, §8):

- `daily_revenue(run_id, prime, date, sky_revenue, sky_revenue_gross,
  sde_revenue, prime_agent_revenue, agent_rate, distribution_rewards,
  chronicle_points, utilized, cum_debt, base_apr, ssr_apy)` — from the
  `sky_revenue_daily` series the pipeline already computes, plus the MtM
  prime revenue per day.
- `venue_daily(run_id, prime, venue_id, date, value_usd, inflow_usd,
  revenue_usd, sd_revenue_usd)`.
- `monthly_headline(run_id, prime, month, basis, results jsonb)` — the
  `provenance.results` block; `basis ∈ {mtd_preview, settled}`.
- Runner mode `--pin yesterday` for every `run_<prime>_2026.py`, reusing
  the fixture/HyperSync sources; the settled run at month-end writes
  `basis='settled'` and remains the one committed to git.
- API: `/v1/primes/{prime}/daily?month=`, `/v1/primes/{prime}/months`,
  `/v1/primes/{prime}/venues?month=`, gated by API key while the month is open.

Acceptance: for a closed month, the MtD run at the month's EoD block
reproduces the settled report to the cent (same code path, same pins).

## 7. Phase 3 — Sky Net Revenue daily

`sky_total` month-to-date: the accrual MSC leg from the phase-2 previews plus
the non-MSC leg run daily (already HyperSync-based). Endpoint
`/v1/sky-total/daily`. Also the natural point to expose the TMF waterfall
preview (`/v1/tmf/waterfall?month=`) since it is a function of SNR.

## 8. Open questions (decide before phase 2)

1. **Dune quota.** Several prime inputs are still Dune (debt, balances, SSR
   for some primes). Daily runs multiply credit use ~30×. Prerequisite to
   move them to HyperSync, or is the quota fine?
2. **Public vs keyed** for daily previews of prime revenue before the MSC
   post is out.
3. **"Daily revenue" semantics**: daily increment (noisy on weekly-NAV RWA
   venues) vs month-to-date cumulative at each day. Storing both is cheap;
   the API default matters.
4. **Where the daily MSC run executes**: Railway cron (same place as the
   API) or GitHub Actions (logs next to the code). Big primes take
   10–20 min; both can hold that.
5. **DR daily**: Distribution Rewards come from the settle-dr-dune monthly
   workbook; a daily form needs its own source.

## 9. Risks

- **Restatement without versioning** would rewrite history silently → §4.1.
- **A preview mistaken for a settled figure** → `basis` on every payload,
  visible labelling in the dashboard.
- **Archive lag**: HyperSync head short of yesterday's EoD → the extractor
  already refuses / flags partial; the cron retries later in the day.
- **Cache poisoning** from RPC hiccups → `read_tmf_state` never-zero guard;
  phase 2 needs the same for its pins.
