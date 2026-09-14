# settlement-cycle

Python pipeline that produces auditable monthly settlement artifacts (`prime_agent_revenue + agent_rate − sky_revenue`) for every Sky prime agent.

Architecture is a 4-stage ETL: **Extract → Normalize → Compute → Load**. Sources are pluggable: Dune for event-aggregation, blockchain RPC for on-chain state snapshots, off-chain APIs for RWA NAVs.

See [PRD.md](PRD.md) for the full design, file structure, migration plan, and open questions.

## Quickstart

```bash
# Clone and install (editable)
git clone git@github.com:soterlabs/settlement-cycle.git
cd settlement-cycle
pip install -e .[dev]

# Set credentials (~/.env or shell)
export DUNE_API_KEY=...
export ETH_RPC=https://eth-mainnet.g.alchemy.com/v2/<key>
export BASE_RPC=https://mainnet.base.org

# Sanity checks
settle version
settle config check --prime obex

# One-off RPC probe (Extract-layer smoke test)
settle debug rpc-balance \
  --chain ethereum \
  --token 0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b \
  --holder 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2

# Full settlement run — writes to settlements/<prime>/<month>/
settle run --prime obex --month 2026-03
```

## Layout

```
settlement-cycle/
├── PRD.md                     ← design doc — read this first
├── README.md                  ← this file
├── CLAUDE.md                  ← collaboration notes auto-loaded by Claude Code
├── QUESTIONS.md               ← open questions (the single source of truth)
├── pyproject.toml
├── docs/                      ← design + reference docs
│   ├── RULES.md
│   ├── SETTLEMENT_ARCHITECTURE.md
│   ├── ASSET_CATALOG.md
│   ├── VALUATION_METHODOLOGY.md
│   ├── ALM_COUNTERPARTIES.md
│   ├── obex/                  ← OBEX README + monthly findings (reconciliation notes)
│   ├── grove/                 ← Phase-2 prime context (PRD, README, QUESTIONS)
│   ├── tmf/                   ← Treasury Management Function report — method + open questions
│   └── {keel,skybase,spark}/   ← Phase-3+ prime READMEs
├── archive/                   ← historical preparation, references, and pricing inputs
├── settlements/<prime>/<month>/  ← generated artifacts (committed to git)
├── settlements/tmf/<month>/      ← TMF waterfall + Smart Burn Engine report (scripts/run_tmf_2026.py)
├── settlements/tmf/data/         ← SBE full-history dataset for msc-dashboard (scripts/build_tmf_history.py)
├── deploy/, .railway/            ← Railway IaC: settle-api + settle-cron (docs/PRD_daily_pipeline_api.md)
├── src/settle/
│   ├── cli.py                 ← argparse entry point
│   ├── domain/                ← Prime, Venue, Period dataclasses
│   ├── extract/               ← Dune, RPC, CoinGecko, issuer APIs (cached)
│   ├── normalize/             ← canonical primitives, source-pluggable
│   ├── compute/               ← pure-Python settlement math
│   ├── load/                  ← Markdown / CSV / provenance writers
│   ├── store/                 ← decoded Postgres facts + `runs` versioning (daily pipeline)
│   ├── api/                   ← settle-api: read-only FastAPI over the store
│   └── validation/            ← schemas + invariant checks
├── queries/                   ← Dune SQL files (parameterized)
├── config/<prime>.yaml        ← per-prime addresses + source choices
└── tests/
```

Settlement artifacts (the per-month Markdown / CSV / provenance produced by `settle run`)
land under `settlements/<prime>/<month>/` in this repo and are git-committed. Path is
configurable via `--output-dir` or the `SETTLE_OUTPUT_DIR` env var.

## Development

### Code

```bash
pip install -e .[dev]
pytest                          # full test suite
pytest tests/unit               # unit tests only
ruff check src tests            # lint
mypy src                        # types
```

### Open questions

Open questions on the pipeline (one per Spark / Grove / BA Labs ask)
are tracked in [`QUESTIONS.md`](QUESTIONS.md) at the repo root. That file
owns both the content of a question and its lifecycle.

Until 2026-09 it was mirrored 1:1 into GitHub Issues, reconciled by a
`scripts/sync_issues.sh`. The mirror is retired: it made every question
edit a two-system operation that could drift, and the issues carried no
discussion the markdown and the PR history did not already hold. Questions
now move like any other change in the repo — in a reviewed commit.

#### The two flows

**Flow A — adding a new question:**

1. Edit `QUESTIONS.md`. Pick the next free Q-ID for the counterparty
   (`G`/`S`/`B` + next number); place under the correct priority
   subsection (P0–P3).
2. Stage and commit.

**Flow B — resolving an existing question:**

1. Move the entry from its priority subsection to `## Resolved` in
   `QUESTIONS.md`, leaving a compact pointer to what the answer was.
2. Add the methodology takeaway to `PRD.md §17.13`.
3. Stage and commit — both edits together, so the resolution and its
   consequence land as one reviewable change.

## Status

Phase 1 complete. The OBEX 2026-03 Dune query
[`agents/obex/queries/obex_monthly_pnl.sql`](archive/reference/obex_monthly_pnl.sql)
was the original reconciliation oracle and matched to within 0.01%. Its e2e
test was retired on 2026-09-01: the 2026-09-01 rate-methodology change (BR is
nominal, see `docs/RULES.md` Rule 1) means the pipeline no longer matches a
query written on the old convention, and past settlements are not restated —
so the oracle could only ever assert a widening gap. Regenerate the SQL on the
current methodology if an independent cross-check is wanted again.
