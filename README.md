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
├── QUESTIONS.md               ← open questions (mirrored 1:1 to GitHub Issues)
├── pyproject.toml
├── scripts/
│   └── sync_issues.sh         ← reconcile QUESTIONS.md ↔ GitHub Issues
├── docs/                      ← design + reference docs
│   ├── RULES.md
│   ├── SETTLEMENT_ARCHITECTURE.md
│   ├── ASSET_CATALOG.md
│   ├── VALUATION_METHODOLOGY.md
│   ├── ALM_COUNTERPARTIES.md
│   ├── valuation_poc/         ← Dune↔Python POC + open questions
│   ├── obex/                  ← OBEX README + monthly findings (reconciliation notes)
│   ├── grove/                 ← Phase-2 prime context (PRD, README, QUESTIONS)
│   └── {keel,prysm,skybase,spark}/   ← Phase-3+ prime READMEs
├── reference/
│   └── obex_monthly_pnl.sql   ← oracle target for Phase-1 e2e test
├── settlements/<prime>/<month>/  ← generated artifacts (committed to git)
├── src/settle/
│   ├── cli.py                 ← argparse entry point
│   ├── domain/                ← Prime, Venue, Period dataclasses
│   ├── extract/               ← Dune, RPC, CoinGecko, issuer APIs (cached)
│   ├── normalize/             ← canonical primitives, source-pluggable
│   ├── compute/               ← pure-Python settlement math
│   ├── load/                  ← Markdown / CSV / provenance writers
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
are tracked in [`QUESTIONS.md`](QUESTIONS.md) at the repo root, mirrored
1:1 into GitHub Issues on this repo. `QUESTIONS.md` owns content;
issues own lifecycle (open ↔ closed) and triage discussion.

#### Tooling

- [`gh` CLI](https://cli.github.com/) authenticated to an account with
  write access to `soterlabs/settlement-cycle`:

  ```bash
  gh auth login
  gh auth status
  ```

#### The two flows

**Flow A — adding a new question:**

1. Edit `QUESTIONS.md`. Pick the next free Q-ID for the counterparty
   (`G`/`S`/`B` + next number); place under the correct priority
   subsection (P0–P3).
2. Run `./scripts/sync_issues.sh --apply`. This creates the GitHub
   issue with the right counterparty + priority labels.
3. Stage and commit.

**Flow B — resolving an existing question:**

1. Comment on the issue with a resolution summary and close it
   (in the GitHub UI). Conversation history stays on the issue.
2. Run `./scripts/sync_issues.sh --apply`. The entry moves from its
   open section to `## Resolved` in `QUESTIONS.md`.
3. Add the methodology takeaway to `PRD.md §17.13`.
4. Stage and commit.

#### Pre-commit hook (recommended)

`scripts/sync_issues.sh --check` reports drift and exits non-zero if
any. Wire it into a `pre-commit` hook so a commit that drifts from
the live issues gets blocked:

```sh
mkdir -p .git/hooks
cat > .git/hooks/pre-commit <<'EOF'
#!/usr/bin/env sh
# Skip when QUESTIONS.md isn't in the staged changes — saves a gh API
# roundtrip on every unrelated commit.
if ! git diff --cached --name-only | grep -q '^QUESTIONS\.md$'; then
  exit 0
fi
exec "$(git rev-parse --show-toplevel)/scripts/sync_issues.sh" --check --quiet
EOF
chmod +x .git/hooks/pre-commit
```

Behaviour:

- Staged change doesn't touch `QUESTIONS.md` → hook is a no-op.
- Staged change touches `QUESTIONS.md` and state is consistent with
  GitHub → commit proceeds.
- Drift detected → commit aborts; the script prints the suggested
  `--apply` invocation. Run it, stage the modified files, re-commit.

The hook is per-clone (`.git/hooks/` is not tracked). Install once
after cloning.

## Status

Phase 1 in progress (see PRD.md §13). OBEX 2026-03 settlement target: match the existing Dune query [`agents/obex/queries/obex_monthly_pnl.sql`](reference/obex_monthly_pnl.sql) within 0.01%.
