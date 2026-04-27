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
├── pyproject.toml
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

```bash
pip install -e .[dev]
pytest                          # full test suite
pytest tests/unit               # unit tests only
ruff check src tests            # lint
mypy src                        # types
```

## Status

Phase 1 in progress (see PRD.md §13). OBEX 2026-03 settlement target: match the existing Dune query [`agents/obex/queries/obex_monthly_pnl.sql`](reference/obex_monthly_pnl.sql) within 0.01%.
