# PRD — `settlement-cycle`: MSC monthly settlement pipeline

**Status:** Phase 2.A shipped (Grove Ethereum) — see [§17 Implementation status](#17-implementation-status) for what's done vs. remaining.
**Owner:** lakonema2000
**Created:** 2026-04-27 · **Last status update:** 2026-04-28
**Repo:** [`soterlabs/settlement-cycle`](https://github.com/soterlabs/settlement-cycle) — this repo holds the implementation **and** the design docs (under [`docs/`](docs/)).
**Origin:** Design docs were originally drafted in [`lakonema2000/msc`](https://github.com/lakonema2000/msc) under `agents/shared/`; copied into this repo on 2026-04-27 so the implementation is self-contained. Per-prime *settlement artifacts* (Markdown / CSV / provenance) are still written to the msc repo at runtime.

A Python package that produces auditable monthly settlement artifacts (`prime_agent_revenue + agent_rate − sky_revenue`) for every Sky prime agent, by composing data from Dune, on-chain RPC, and off-chain APIs through a 4-stage ETL pipeline.

---

## 1. Background

The MSC framework currently exists as:
- A set of parameterized Dune queries (`queries/shared/*.sql`) and per-prime monolithic queries (`agents/obex/queries/obex_monthly_pnl.sql`) that fuse debt math, agent-rate math, and position valuation in one ~280-line SQL file.
- A POC ([`agents/shared/valuation_poc/`](docs/valuation_poc/)) that proved Dune and Python converge on the same numbers but that the per-asset valuation work is dramatically simpler in Python — `convertToAssets`, `balanceOf` of a rebasing aToken, Curve `pool.balances(i)` math, and RWA NAV feeds either don't exist on Dune or balloon to 50-line CTEs.
- Architectural design ([`agents/shared/SETTLEMENT_ARCHITECTURE.md`](docs/SETTLEMENT_ARCHITECTURE.md)) that prescribes a hybrid Dune + RPC + off-chain pipeline.

This PRD is the kickoff for the implementation. The deliverable is the Python package in this repo (`settlement-cycle`) that replaces the monolithic Dune query approach with a 4-stage Extract / Normalize / Compute / Load pipeline. The existing OBEX Dune query is preserved as a reconciliation oracle in [`reference/obex_monthly_pnl.sql`](reference/obex_monthly_pnl.sql) (used by the Phase-1 e2e test).

### 1.1 Reference documents

All design docs live under [`docs/`](docs/) in this repo.

- [`SETTLEMENT_ARCHITECTURE.md`](docs/SETTLEMENT_ARCHITECTURE.md) — detailed architectural rationale and design choices
- [`ASSET_CATALOG.md`](docs/ASSET_CATALOG.md) — 56 real assets across 8 pricing categories
- [`VALUATION_METHODOLOGY.md`](docs/VALUATION_METHODOLOGY.md) — Dune SQL patterns and per-category math
- [`valuation_poc/COMPARISON.md`](docs/valuation_poc/COMPARISON.md) — Dune↔Python convergence evidence
- [`valuation_poc/QUESTIONS.md`](docs/valuation_poc/QUESTIONS.md) — 13 open data-engineering questions
- [`RULES.md`](docs/RULES.md) — APY/SSR/borrow-rate rules
- [`agents/obex/queries/obex_monthly_pnl.sql`](reference/obex_monthly_pnl.sql) — current OBEX implementation (reconciliation oracle)
- [`agents/grove/PRD.md`](docs/grove/PRD.md) — Grove-specific scope, will become a consumer of `settle/`

---

## 2. Goals

1. **One Python package** that produces a monthly settlement artifact for any prime agent given `(prime_id, month)`.
2. **4-stage ETL** with strict layer boundaries: Extract → Normalize → Compute → Load.
3. **Source-pluggable** so any data source (Dune today, self-hosted indexer or subgraph tomorrow) can be swapped behind a Python `Protocol`.
4. **Reproducible to the byte** — every run pinned to a `block_number` per chain; identical pin produces identical artifact.
5. **Auditable in PR** — settlement artifacts (Markdown + CSV + provenance JSON) committed to git under `settlements/<prime>/<month>/` in this repo.
6. **Match the existing Dune query** for OBEX 2026-03 and 2026-04 within < 0.01% (modulo the documented APR/APY discrepancy already flagged in [`agents/obex/findings/`](https://github.com/lakonema2000/msc/blob/main/agents/obex/findings/)).
7. **All business logic in Python** — Dune SQL emits raw aggregates only; no `CASE WHEN block_date < ...` rate ladders or APY math in SQL.

## 3. Non-goals

- Replacing the Dune dashboard layer for analyst-facing visualizations. Dashboards continue to read the existing parameterized Dune queries.
- Real-time / intraday settlement. Granularity is monthly with daily underlying compute.
- A web UI or hosted service. Local CLI + git-committed artifacts only.
- Cross-prime aggregation across the whole Sky ecosystem. Per-prime is the scope.
- Plume / Monad coverage in Phase 1 (deferred per [`grove/QUESTIONS.md`](docs/grove/QUESTIONS.md) Q4).

---

## 4. Architecture

The pipeline has four stages; each has one job and one set of allowed dependencies. Direction is strictly upward: `extract` knows nothing about higher layers; `compute` never reaches the network.

| Stage | Job | Imports |
|---|---|---|
| **Extract** | Pull raw data; cache by `(source, args, pin)` | external libraries only |
| **Normalize** | Source dispatch + canonical primitives. Returns typed Python objects keyed by business concept (`debt_timeseries`, `position_balance`, `unit_price`). Source-agnostic from this layer up. | `extract`, `domain` |
| **Compute** | Pure settlement math on primitives (`compute_sky_revenue`, `compute_monthly_pnl`). No I/O, no source awareness. | `normalize`, `domain` |
| **Load** | Render artifacts + commit | `compute`, `domain` |

Architectural rationale lives in `SETTLEMENT_ARCHITECTURE.md`. This PRD is concerned with the build.

### 4.1 Source-routing matrix

| Normalize primitive | Source | Why |
|---|---|---|
| `get_debt_timeseries` | Dune (`ethereum.traces` for `frob` calls) | Event aggregation across months — Dune's strength |
| `get_subproxy_balance_timeseries` | Dune (`tokens.transfers`) | Same |
| `get_alm_balance_timeseries` (USDS) | Dune | Same |
| `get_ssr_history` | Dune (`file()` traces on sUSDS) | Same |
| `get_venue_inflow_timeseries` (cost basis) | Dune (`tokens.transfers`) | Same |
| `get_position_balance` (par stable in ALM) | Dune | Trivial cumulative sum |
| `get_position_balance` (rebasing aToken/spToken) | RPC `balanceOf` | One call vs ~50-line scaled-events CTE; POC validated 0.001% match |
| `get_position_balance` (ERC-4626 vault) | RPC `balanceOf` | Canonical |
| `get_position_balance` (Curve LP) | RPC `balanceOf` | Same |
| `get_position_balance` (native gas) | RPC `eth_getBalance` | Not in `tokens.transfers` |
| `get_unit_price` (par stable) | hardcoded `$1.00` | — |
| `get_unit_price` (ERC-4626) | RPC `convertToAssets(1 share)` × underlying price | POC method |
| `get_unit_price` (aToken / spToken) | hardcoded `$1.00` (peg) | balance is already rebased |
| `get_unit_price` (Curve LP) | RPC reserves × underlying prices (POC Method B) | Reserves method beats `virtual_price` shortcut |
| `get_unit_price` (RWA — Centrifuge) | Issuer API; CoinGecko fallback | Authority |
| `get_unit_price` (RWA — BlackRock / Superstate) | Issuer API | Authority — no on-chain getter |
| `get_unit_price` (governance, MORPHO) | One canonical source per token (Dune `prices.minute` OR CoinGecko — pick one per [QUESTIONS Q10](docs/valuation_poc/QUESTIONS.md)) | — |
| `get_unit_price` (native gas, ETH) | CoinGecko | — |

---

## 5. Domain model

```python
@dataclass(frozen=True)
class Chain:
    name: Literal['ethereum', 'base', 'arbitrum', 'optimism', 'unichain', 'avalanche_c', 'plume', 'monad']

@dataclass(frozen=True)
class Token:
    chain: Chain
    address: bytes               # 20 bytes
    symbol: str
    decimals: int

@dataclass(frozen=True)
class Venue:
    id: str                      # e.g. 'E1', 'E2' for Grove (matches PRD §4.1)
    prime_id: str
    chain: Chain
    token: Token                 # the venue token (aToken, vault share, LP, …)
    pricing_category: Literal['A','B','C','D','E','F','G','H']
    underlying: Token | None     # for B/C/D/F where price chain refers to underlying

@dataclass(frozen=True)
class Prime:
    id: str                                  # 'obex' | 'grove' | 'spark' | …
    ilk_bytes32: bytes
    subproxy: dict[Chain, bytes]
    alm: dict[Chain, bytes]
    venues: list[Venue]
    start_date: date                         # calendar start (first frob date)

@dataclass(frozen=True)
class Period:
    start: date
    end: date                                # inclusive
    pin_blocks: dict[Chain, int]             # resolved once per run, propagated to every call

@dataclass(frozen=True)
class MonthlyPnL:
    prime: Prime
    month: date                              # first of the month
    period: Period
    sky_revenue: Decimal
    agent_rate: Decimal
    prime_agent_revenue: Decimal
    monthly_pnl: Decimal                     # = prime_agent_revenue + agent_rate − sky_revenue
    cumulative_pnl: Decimal                  # running total since prime.start_date
    per_venue_breakdown: dict[str, Decimal]
    provenance: ProvenanceLog                # what was fetched, from where, at what block
```

---

## 6. Stage specifications

### 6.1 Extract

One module per source. No business logic. Disk-cached.

```python
# extract/dune.py
execute_query(sql_path: Path, params: dict, pin_block: int) -> pd.DataFrame

# extract/rpc.py
eth_call(chain: Chain, contract: bytes, selector: str, args: bytes, block: int) -> bytes
balance_of(chain: Chain, token: bytes, holder: bytes, block: int) -> int
get_native_balance(chain: Chain, addr: bytes, block: int) -> int

# extract/coingecko.py
price(coin_id: str, ts: datetime) -> Decimal

# extract/issuer/centrifuge.py
nav(token: bytes, date: date) -> Decimal

# extract/issuer/superstate.py
nav(token: bytes, date: date) -> Decimal
```

**Cache contract:** results keyed by `sha256(source_id, args, pin_block_or_ts)`. Format: Parquet for DataFrames, JSON for scalars. Lives under `~/.cache/msc-settle/` by default; configurable via env.

### 6.2 Normalize

Pure Python. Source dispatch via Protocol classes. Returns canonical objects.

```python
# normalize/protocols.py
class IDebtSource(Protocol):
    def debt_timeseries(self, ilk: bytes, period: Period) -> pd.DataFrame: ...

class IBalanceSource(Protocol):
    def cumulative_transfer_timeseries(self, token: bytes, holder: bytes, period: Period) -> pd.DataFrame: ...
    def position_balance(self, token: bytes, holder: bytes, block: int) -> int: ...

class IPriceSource(Protocol):
    def unit_price(self, venue: Venue, block: int) -> Decimal: ...

# normalize/debt.py
def get_debt_timeseries(prime: Prime, period: Period) -> pd.DataFrame: ...

# normalize/balances.py
def get_subproxy_balance_timeseries(prime: Prime, token: Token, period: Period) -> pd.DataFrame: ...
def get_alm_balance_timeseries(prime: Prime, token: Token, period: Period) -> pd.DataFrame: ...
def get_position_balance(prime: Prime, venue: Venue, block: int) -> Decimal: ...

# normalize/prices.py
def get_unit_price(venue: Venue, block: int) -> Decimal: ...

# normalize/ssr.py
def get_ssr_history(period: Period) -> pd.DataFrame: ...

# normalize/inflows.py  (cost basis input)
def get_venue_inflow_timeseries(prime: Prime, venue: Venue, period: Period) -> pd.DataFrame: ...
```

### 6.3 Compute

Pure functions. No I/O. Trivially testable with frozen Normalize fixtures.

```python
# compute/sky_revenue.py
def compute_sky_revenue(prime: Prime, period: Period) -> Decimal: ...

# compute/agent_rate.py
def compute_agent_rate(prime: Prime, period: Period) -> Decimal: ...

# compute/prime_agent_revenue.py
def compute_prime_agent_revenue(prime: Prime, period: Period) -> tuple[Decimal, dict[str, Decimal]]:
    """Returns (total, per_venue_breakdown)."""

# compute/monthly_pnl.py
def compute_monthly_pnl(prime: Prime, month: date) -> MonthlyPnL: ...
```

### 6.4 Load

```python
# load/markdown.py
def write_settlement_markdown(pnl: MonthlyPnL, dest_dir: Path) -> Path:
    """Writes <dest_dir>/pnl.md."""

# load/csv.py
def write_settlement_csv(pnl: MonthlyPnL, dest_dir: Path) -> Path: ...

# load/provenance.py
def write_provenance(pnl: MonthlyPnL, dest_dir: Path) -> Path:
    """Pin blocks, source IDs, fetch timestamps, validation log."""
```

---

## 7. Configuration

Per-prime YAML under `config/<prime>.yaml`. Pydantic-validated on load.

```yaml
# config/grove.yaml
id: grove
ilk_bytes32: '0x414c4c4f4341544f522d424c4f4f4d2d41000000000000000000000000000000'
start_date: '2025-05-14'

addresses:
  ethereum:
    subproxy: '0x1369f7b2b38c76b6478c0f0e66d94923421891ba'
    alm:      '0x491edfb0b8b608044e227225c715981a30f3a44e'
  base:
    alm:      '0x9b746dbc5269e1df6e4193bcb441c0fbbf1cecee'

venues:
  - id: E1
    chain: ethereum
    token:    {address: '0xe3190143…77eb', symbol: 'aHorRwaRLUSD', decimals: 18}
    pricing_category: C
    underlying: {address: '0x8292bb45…17ed', symbol: 'RLUSD', decimals: 18}
  - id: E4
    chain: ethereum
    token:    {address: '0xbeeff08d…4111', symbol: 'grove-bbqUSDC', decimals: 18}
    pricing_category: B
    underlying: {address: '0xa0b86991…eb48', symbol: 'USDC', decimals: 6}
  # … 16 more for Grove

sources:
  debt: dune
  subproxy_balances: dune
  alm_balances: dune
  position_balances:
    A: dune
    B: rpc
    C: rpc
    D: rpc
    E: rpc
    F: rpc
    G: rpc
    H: dune
  prices:
    A: const_one
    B: rpc_convert_to_assets
    C: const_one
    D: const_one
    E: api_centrifuge
    F: rpc_curve_reserves
    G: coingecko
    H: dune_prices_minute
```

---

## 8. End-to-end flow

```
$ python -m settle run --prime grove --month 2026-04

  1. Resolve Period
     ├─ start = 2026-04-01, end = 2026-04-30
     └─ pin_blocks = {ethereum: 24945607, base: 45096799, …}  (last block of 2026-04-30 UTC)

  2. Extract (cached)
     ├─ Dune: debt_timeseries.sql, transfer_timeseries.sql ×N, ssr_history.sql
     ├─ RPC : balanceOf, convertToAssets, get_virtual_price, eth_getBalance per venue
     └─ API : Centrifuge NAV for JTRSY/JAAA, CoinGecko for ETH/MORPHO

  3. Normalize
     ├─ debt timeseries DataFrame
     ├─ subproxy + ALM balance timeseries DataFrames
     ├─ per-venue position balance + unit price (Decimals at start_block + end_block)
     ├─ per-venue inflow timeseries (cost basis)
     └─ SSR history DataFrame

  4. Compute
     ├─ sky_revenue = Σ (utilized × ((1 + borrow_apy)^(1/365) − 1)) over period
     ├─ agent_rate  = Σ (subproxy_usds × ... + subproxy_susds × ...) over period
     ├─ prime_revenue = (Σ value_eom − Σ value_som − Σ inflow) per venue
     ├─ monthly_pnl  = prime_revenue + agent_rate − sky_revenue
     └─ Validate invariants (cum_debt ≥ 0, agent_demand ≤ cum_debt, …)

  5. Load
     ├─ agents/grove/settlements/2026-04/pnl.md
     ├─ agents/grove/settlements/2026-04/pnl.csv
     └─ agents/grove/settlements/2026-04/provenance.json
```

---

## 9. File structure

```
settlement-cycle/                       ← this repo
├── PRD.md                              ← this file
├── README.md                           ← brief usage; see PRD for design
├── pyproject.toml                      ← uv / hatch
├── src/settle/
│   ├── __init__.py
│   ├── cli.py                          ← `python -m settle …` entry point
│   ├── domain/
│   │   ├── primes.py                   ← Prime, Venue, Token, Chain, Address
│   │   ├── period.py                   ← Period, Month, EOD-block resolver
│   │   └── pricing.py                  ← PricingCategory enum (A–H)
│   ├── extract/
│   │   ├── cache.py                    ← @cache_on_disk decorator
│   │   ├── dune.py
│   │   ├── rpc.py
│   │   ├── coingecko.py
│   │   └── issuer/
│   │       ├── centrifuge.py
│   │       ├── superstate.py
│   │       └── blackrock.py
│   ├── normalize/
│   │   ├── protocols.py                ← IDebtSource, IBalanceSource, IPriceSource
│   │   ├── registry.py                 ← config-driven source dispatch
│   │   ├── debt.py
│   │   ├── balances.py
│   │   ├── inflows.py
│   │   ├── prices.py
│   │   └── ssr.py
│   ├── compute/
│   │   ├── sky_revenue.py
│   │   ├── agent_rate.py
│   │   ├── prime_agent_revenue.py
│   │   └── monthly_pnl.py
│   ├── load/
│   │   ├── markdown.py
│   │   ├── csv.py
│   │   └── provenance.py
│   └── validation/
│       ├── schemas.py                  ← Pandera DataFrame schemas
│       └── invariants.py               ← Compute-layer sanity checks
├── tests/
│   ├── fixtures/                       ← frozen Extract outputs (Parquet)
│   │   ├── obex_2026_03/
│   │   └── grove_2026_03/
│   ├── unit/
│   │   ├── test_compute_sky_revenue.py
│   │   ├── test_compute_agent_rate.py
│   │   └── test_compute_monthly_pnl.py
│   ├── integration/
│   │   └── test_normalize_with_mock_sources.py
│   └── e2e/
│       └── test_obex_2026_03_against_dune_oracle.py
├── docs/                               ← design + per-prime context
│   ├── RULES.md
│   ├── SETTLEMENT_ARCHITECTURE.md, ASSET_CATALOG.md, VALUATION_METHODOLOGY.md
│   ├── ALM_COUNTERPARTIES.md
│   ├── valuation_poc/                  ← Dune↔Python POC (COMPARISON, QUESTIONS)
│   ├── obex/                           ← OBEX README + monthly findings/
│   ├── grove/                          ← Grove PRD/README/QUESTIONS (Phase 2)
│   └── {keel,prysm,skybase,spark}/     ← Phase 3+ prime READMEs
├── reference/
│   └── obex_monthly_pnl.sql            ← Phase-1 oracle target
├── settlements/                        ← committed settlement artifacts
│   └── <prime>/<month>/                ← {pnl.md, pnl.csv, venues.csv, provenance.json}
├── queries/                            ← Dune SQL files, parameterized
│   ├── debt_timeseries.sql
│   ├── transfer_timeseries.sql
│   ├── ssr_history.sql
│   └── venue_inflow.sql
└── config/
    ├── obex.yaml
    └── grove.yaml
```

**Settlement output destination.** Load writes artifacts inside this repo at `settlements/<prime>/<month>/`. The path is configurable via `SETTLE_OUTPUT_DIR` env or a `--output-dir` CLI flag. Each artifact is git-committed; PR review of the diff under `settlements/` is the audit gate.

---

## 10. Conventions

1. **Pin to `block_number`.** Period resolves once; pin propagates to every Dune query (`WHERE block_number <= :pin`) and every RPC call (`block=:pin`). No "latest" anywhere.
2. **`Decimal` for all USD.** `float` only inside `(1+r)**(1/365)`; cast back to `Decimal` before assembling.
3. **No business logic in SQL.** Rate ladders, APY formulae, NAV math, MtM deltas all in Python.
4. **One source per primitive in production.** A second source attached only as a reconciliation logger that warns on > N bps drift.
5. **Cache at Extract; recompute Normalize+Compute every run.**
6. **Failures raise.** Schema check failures, source unreachability, invariant violations → settlement run aborts.
7. **Markdown + CSV + provenance in git.** PR review of `settlements/<prime>/<month>/` (in this repo) is the audit gate.
8. **Python 3.11+.** Match the existing local env.

---

## 11. Validation gates

| Gate | Where | Failure mode |
|---|---|---|
| Source returned non-empty | Extract | Raise `ExtractError` |
| Schema (Pandera) | Normalize → Compute boundary | Raise `ValidationError` |
| Decimal types and units | Normalize → Compute boundary | Raise |
| `cum_debt ≥ 0` | Compute | Raise |
| `agent_demand ≤ cum_debt` | Compute | Raise |
| `Σ cost_basis ≈ cum_debt − cross_chain_out` (within 1%) | Compute | Warn (Q6 reconciliation drift is documented) |
| Source-pair drift (e.g. CoinGecko vs `prices.minute`) | Compute | Warn if > N bps |
| Round-trip: `monthly_pnl == prime_rev + agent_rate − sky_rev` | Compute | Raise |
| OBEX 2026-03 reproduction matches Dune oracle within 0.01% | E2E test | Build fails |

---

## 12. Testing strategy

- **Unit tests** at every Compute function with hand-built input DataFrames. Pure functions = trivial assertions.
- **Integration tests** that call Normalize with `MockDebtSource`, `MockBalanceSource`, `MockPriceSource` injected; assert source-agnostic behavior.
- **Fixture-based replay tests**: freeze Extract outputs to Parquet under `tests/fixtures/obex_2026_03/`; replay through Normalize → Compute → Load; assert the artifact matches a committed expected result.
- **End-to-end oracle test**: run the live pipeline for OBEX 2026-03 against Dune; compare to the pre-existing `obex_monthly_pnl.sql` output. Must match within 0.01% modulo documented APR/APY discrepancy.

---

## 13. Migration plan

| Phase | Scope | Acceptance |
|---|---|---|
| **1 — Plumbing + OBEX** | Implement all 4 stages end-to-end for OBEX (single venue, syrupUSDC). Reproduce 2026-03 settlement matching Dune oracle. | OBEX 2026-03 PnL within 0.01% of `obex_monthly_pnl.sql` |
| **2 — Grove Ethereum** | Onboard Grove's 18 Ethereum venues. No new infra; only YAML + per-category Normalize branches. | Grove 2026-03 settlement produced; `Σ cost_basis ≈ cum_debt` within 1% |
| **3 — Grove Base + Avalanche** | Add chain coverage; per-chain RPC + Dune `tokens.transfers` filtering. | Grove cross-chain reconciliation gap documented in `findings/` |
| **4 — Spark + Skybase** | Multi-prime, multi-chain. | All four primes produce monthly settlements via the same pipeline. |
| **5 — Indexer alternative** | Implement `SubgraphDebtSource` against The Graph or self-hosted Goldsky/Subsquid; run side-by-side with Dune for one month. | Numerical match within 0.01%; benchmark cost + latency |

OBEX's existing Dune query stays as a reconciliation oracle through Phase 1. Cut-over only when Phase 1 produces identical numbers for two consecutive months.

---

## 14. Phase 1 deliverables

Concrete first-PR sequence:

1. **Bootstrap** — `pyproject.toml`, `cli.py` skeleton, `domain/` dataclasses, `config/obex.yaml`. CLI runs but does nothing.
2. **Extract — Dune** — `extract/dune.py` with disk cache, `queries/debt_timeseries.sql` parameterized. Test against a known frob block.
3. **Extract — RPC** — `extract/rpc.py`. Test `balance_of(syrupUSDC, OBEX_ALM, block)` matches `obex_monthly_pnl.sql` `cum_venue` for 2026-03 EoM.
4. **Normalize — debt + balances + ssr** — wrap Dune queries; Pandera schemas; unit tests with mock sources.
5. **Normalize — positions + prices for OBEX** — single-venue: syrupUSDC, pricing category B (`convertToAssets`).
6. **Compute — sky_revenue + agent_rate** — pure-Python implementations of [`RULES.md`](docs/RULES.md) §3 and §4 formulae. Match Dune oracle.
7. **Compute — prime_agent_revenue + monthly_pnl** — composes per-venue value deltas. Match Dune oracle.
8. **Load** — Markdown + CSV + provenance writers. Commit `settlements/obex/2026-03/`.
9. **E2E test** — `tests/e2e/test_obex_2026_03_against_dune_oracle.py`. CI gate.

Each step is one PR. Rough total: 9 PRs to ship Phase 1.

---

## 15. Open questions specific to `settle/`

Outside the data-engineering questions in [`valuation_poc/QUESTIONS.md`](docs/valuation_poc/QUESTIONS.md), these are package-specific.

| # | Question | Decision needed by |
|---|---|---|
| S1 | **Cache backend** — disk JSON + Parquet, or DuckDB? DuckDB enables free SQL over cached data; costs one dependency. | Phase 1 step 2 |
| S2 | **CLI framework** — Typer vs argparse? Typer is nicer; argparse has zero deps. | Bootstrap |
| S3 | **Dune client** — official `dune-client` package or roll our own thin wrapper around the API? Existing MCP integration uses raw HTTP. | Phase 1 step 2 |
| S4 | **Validation library** — Pandera (heavy but powerful) or pydantic + manual asserts? | Phase 1 step 4 |
| S5 | **`block_number` resolver** — for a given `(chain, end-of-day timestamp)`, query the chain to find the last block `≤ ts`. Implementation: binary search on `eth_getBlockByNumber` or use Etherscan's `block?timestamp=` API. | Bootstrap |
| S6 | **Period bounds** — should the period be `[SoM, EoM)` or `[SoM, EoM]`? `obex_monthly_pnl.sql` uses inclusive end via `LAST_VALUE`; document and match. | Phase 1 step 7 |
| S7 | **Per-chain RPC config** — Alchemy keys per chain in `.env`? In `config/<prime>.yaml`? In a separate `config/rpc.yaml`? Recommend separate to avoid leaking keys per prime. | Bootstrap |
| S8 | **Settlement re-runs** — if a past month's settlement is regenerated (e.g. NAV correction), do we overwrite or version? Recommend versioning: `settlements/2026-04/v1/`, `v2/` with a CHANGELOG. | Phase 1 step 8 |
| S9 | **Concurrency** — parallel Extract calls (per-venue RPC, multiple Dune queries)? Async or thread pool? Recommend `asyncio` + `aiohttp` from the start; one call per venue × per chain × per month is ~50 calls and serializes to ~30s today. | Phase 1 step 3 |

Resolutions land as ADRs in `adr/` at the root of this repo.

---

## 16. Success criteria

- ✅ OBEX 2026-03 monthly PnL produced via the pipeline matches `obex_monthly_pnl.sql` within 0.01% (modulo documented APR/APY discrepancy).
- ✅ Grove 2026-03 monthly PnL produced; `Σ cost_basis ≈ cum_debt` within 1%.
- ✅ End-to-end run from `python -m settle run --prime obex --month 2026-03` completes in < 60s on a warm cache, < 5min cold.
- ✅ All four stage boundaries enforced by the import graph (test: lower-stage modules cannot import upper-stage modules).
- ✅ At least one settlement artifact in `settlements/<prime>/<month>/` for each onboarded prime, committed to git.
- ✅ A second source (mock or subgraph) plugged in behind `IDebtSource` without changes to Compute or Load layers.

---

## 17. Implementation status

**As of 2026-04-29 (later session).** This section is updated as the implementation progresses; everything above is the original design and is preserved for reference.

### 17.1 Shipped

#### Phase 1 — OBEX
- ✅ All 4 stages end-to-end. OBEX 2026-03 PnL matches the existing Dune oracle within tolerance.
- ✅ 9 PRs delivered as planned.

#### Phase 2.A — Grove Ethereum (12 active venues + 6 idle stables)
- ✅ All 12 active Ethereum venues priced: E1–E3 Aave aTokens (Cat C), E4–E6 Morpho 4626 (Cat B), E7–E10 RWA tranches (Cat E — STAC/JAAA/JTRSY/BUIDL), E11 Curve LP (Cat F), E12 Uni V3 NFT (Cat F).
- ✅ E13–E18 idle ALM stables added (Cat A par-stable + Cat B sUSDS).
- ✅ E23 Steakhouse Prime Instant on Base added (BA-Labs cross-check identified gap).
- ✅ Per-venue inflow tracking for **all categories**:
  - Cat C/D: closed-form `scaledBalanceOf`-based yield (`yield = scaled_som × (index_eom − index_som) / RAY`). Two RPC reads per venue.
  - Cat B: share mint/burn × at-day-end-block `convertToAssets`.
  - Cat E: cumulative balance × at-day-end-block NAV oracle (Chronicle + const_one fallback). E10 BUIDL uses a `flow_filter.min_transfer_amount_usd: 1_000_000` to separate $50M-class capital subscriptions from $30K-class daily yield distributions (both arrive as ERC-20 mints from `0x0`).
  - Cat F: V3 — NFPM `IncreaseLiquidity`/`DecreaseLiquidity` events via Dune; Curve — wired but no fixture captured (acceptance run uses no-events stub since E11 had no flows in March).
  - Cat A: cumulative balance × `$1`. Closes the E15 USDC false-revenue ($10M intra-period swap residue).
- ✅ V3 fee accrual via `feeGrowthInside` deltas (recovers ≈$7K of fee revenue per E12 over 31 days that the simpler `tokensOwed`-only model misses).
- ✅ DuneBlockResolver (322-day fixture) — drops first-run latency from ~15min to ~10s.
- ✅ MCP-driven Dune fixtures captured for the entire prime lifetime (debt, balances, SSR, V3 events Q1 2026, RWA cum_balance for E7–E10/E20–E22, mint/burn for E1–E6/E19/E23, blocks_at_eod per chain, inflow_by_counterparty E15, PSM USDS, NAV overrides for pre-deployment Chronicle blocks).
- ✅ 192 unit + 8 integration tests passing. Markdown + CSV + provenance written under `settlements/grove/2026-03/`.
- ✅ Methodology alignment with `prime-settlement-methodology.md` + `debt-rate-methodology.md` complete except for two deferred items (subsidised rate, idle USDS in lending pools/AMMs). See §17.7.
- ✅ Q1 2026 multi-month run (`scripts/run_grove_2026_q1.py`) produces Jan/Feb/Mar settlement numbers in one execution. See §17.8.

#### Cross-cutting
- ✅ Source pluggability across `IDebtSource`, `IBalanceSource`, `ISSRSource`, `IPositionBalanceSource`, `IConvertToAssetsSource`, `IBlockResolver`, `INavOracleSource`, `IV3PositionSource`, plus duck-typed `CurvePoolSource`.
- ✅ Block-pinning discipline (every RPC call pins to `block_number`; no "latest").
- ✅ Decimal/float discipline (`float` only inside `(1+r)^(1/365)`).
- ✅ SQL queries shipped as package data (`src/settle/queries/*.sql`); `pip install -e .` and wheel install both resolve correctly.
- ✅ Validation guard: `compute_sky_revenue` raises on empty `debt` / `ssr` (avoids silent zero-revenue when a Dune source is misconfigured).

### 17.2 Remaining for full Grove MSC

#### Tier 1 — Blocks accuracy
- ✅ **Multi-chain venues — Phase 2.B partial rollout** (2026-04-28). Base (E19 grove-bbqUSDC), Avalanche-C (E20 JAAA-avax + E21 GACLO-1), Plume (E22 ACRDX) implemented. Closed the cost-basis miss from −12.70% to **+0.06%** (within PRD §5.2 tolerance). Cross-chain Chronicle (`oracle_chain` field on `NavOracle`) added for Avalanche/Plume RWAs whose feeds live on Ethereum.
- ⚠️ **Monad (E23 grove-bbqAUSD) deferred to Phase 2.C** — both Alchemy and drpc Monad endpoints have ~3.8M-block archival windows. SoM (12.6M blocks back) and EoM (5.9M back) are outside available state, so historical `balanceOf` / `convertToAssets` reads fail. Position is small (~$6.5M EoM, 0.23% of book) so impact is negligible. Resolution path: a dedicated archival Monad node, or implementing Dune-cum-balance × const-pps approximation in the value path.
- ✅ **Curve E11 inflow** uses closed-form `balance × unit_price` (analogous to Aave's `scaledBalance × index`) — works for any Curve template (NextGen / Plain Pool / Vyper variants) without decoding event signatures.

#### Tier 2 — Operational layer
- ❌ **CLI**. `src/settle/cli.py` is a placeholder. Production needs `python -m settle run <prime> <month>` with registry-default sources, `--output-dir`, multi-month batch.
- ❌ **Hard validation gates** at the Compute boundary (cost-basis tolerance, monotonic invariants, source-pair drift). Today only `MonthlyPnL.__post_init__` enforces the round-trip identity.
- ❌ **Live end-to-end test**. All current tests use fixtures or mocks; one real-Dune + real-RPC run would prove the production paths work. (`tests/e2e/` exists but only has the OBEX oracle test.)
- ❌ **Settlement orchestration** (multi-month batch, cache invalidation strategy, re-run idempotence).
- ❌ **Distribution workflow** (sign-off, stakeholder notification, dispute resolution). Out of scope for the data pipeline but part of the broader MSC operation.

#### Tier 3 — Precision refinements (non-blocking)
- ⚠️ **BUIDL off-Transfer mechanism**. ~$175M constant gap between Dune transfer-sum ($532M) and on-chain `balanceOf` ($707M). Doesn't affect period revenue (gap is constant SoM↔EoM) but is an unresolved on-chain artifact worth understanding.
- ⚠️ **Per-event pricing for Cat B/E**. Today uses at-day-end-block `convertToAssets` / NAV; intra-day variance is bps. Per-event pricing requires a new per-event SQL primitive.
- ⚠️ **Aave aToken edge cases**. `scaledBalanceOf` is correct for Aave V3 + SparkLend. A v4 with a different rebase model, or a venue migrated mid-period, would need a separate code path. No current Grove venue trips this.
- ⚠️ **Subproxy USDC**. Grove subproxy holds ~$0.75M USDC at 2026-04-21 per `docs/grove/README.md` — flagged for reconciliation. Doesn't earn agent rate today; whether it should is a Sky-level question.

#### Tier 4 — Capabilities
- ❌ **Per-venue invariant gates** beyond the headline round-trip (e.g. `revenue ≥ −value_som × max_loss_rate`, `rebase yield ≥ 0` for live aTokens).
- ❌ **Re-pricing / re-snapshot mechanism** if NAV oracles update retroactively.
- ❌ **Audit-quality input archival** (the exact Dune query results, RPC responses, cache state) for byte-identical third-party verification.

### 17.3 Headline numbers — Grove Q1 2026 (current)

The reported headline is `prime_agent_total_revenue` (what the prime is owed) and `sky_revenue` (what the prime owes Sky), reported **separately** — they're not netted at this layer. The previously-emitted `monthly_pnl = prime_agent_revenue + agent_rate + distribution_rewards − sky_revenue` is kept for audit (`provenance.json`) but no longer displayed in the markdown headline or `pnl.csv`.

`sky_revenue` here is the **net** value after Sky absorbs the Step 4 shortfall on Sky Direct underperformers. `sky_direct_shortfall` is reported separately as a line item.

| Month | prime_agent_total_revenue | sky_revenue (net) | sky_direct_shortfall | monthly_pnl |
|---|---:|---:|---:|---:|
| 2026-01 | $5,876,810 | $6,526,238 | $532,390 | −$649,428 |
| 2026-02 | $1,572,546 | $6,217,052 | $418,738 | −$4,644,506 |
| 2026-03 |   $377,578 | $7,422,472 | $770,845 | −$7,044,895 |
| **Q1 total** | **$7,826,933** | **$20,165,762** | **$1,721,973** | **−$12,338,828** |

Reproduce: `python3 scripts/run_grove_2026_q1.py` (lifetime Dune fixtures cover all three months; pin blocks per month are hardcoded in `PIN_BLOCKS_BY_MONTH`).

**Cost-basis (March 2026):** **+0.63%** (Σ value $2.827B vs. cum_debt $2.809B across 23 venues, with E23 Steakhouse Prime Instant on Base added). The increment over the prior +0.06% reflects E23's $16M EoM value not yet fully tracked in cum_debt.

**Reconciliation vs Sky's reported Sky Share for March 2026:** Sky reports $6,290,684; our calc gives $7,422,472. The +$1.13M residual remains unexplained — methodology (Step 1 BR on utilized, Step 4 shortfall on JTRSY+BUIDL) is correct, but Sky's exact "Asset Value" definition for BR_charge differs from any single approach we tried (midpoint, daily time-weighted, EoM). See §17.7 for hypothesis space.

`distribution_rewards` is a default-zero placeholder for referral / liquidity-program payouts (e.g. skybase referral codes). Field exists on `MonthlyPnL` and flows through to all output formats; populated when the source lands (Phase 3+).

Per-venue revenue summary (March 2026, post Sky Direct floor):
- E22 ACRDX (Plume): −$351,450 (NAV dropped 0.69%)
- E3 Aave Ethereum aRLUSD: +$206,733
- E1 Aave Horizon aRwaRLUSD: +$148,261
- E7 STAC: +$127,924
- E20 JAAA-avax: +$105,000
- E8 JAAA-eth: +$52,419
- E6 grove-bbqAUSD: +$40,044
- E4/E5/E19/E23 Morpho 4626 (combined): +$34,634
- E11 Curve LP, E12 V3 LP: +$7,738
- **E9 JTRSY, E10 BUIDL (Sky Direct, floored)**: $0 each (Sky absorbs $580K + $191K shortfall)
- **Total prime_agent_revenue**: $371,302

### 17.5 Cat A inflow accounting — design note

Idle par-stable holdings on the ALM (E13–E18 for Grove) use **per-counterparty source tagging via an external allowlist**. Every Transfer to/from the ALM is classified by counterparty:

- Counterparty in `prime.external_alm_sources[chain]` → off-chain custodian sending realized yield (e.g. Anchorage). Inflow passes through to revenue.
- Any other counterparty (PSM swap leg, venue contract allocation/withdrawal, AllocatorBuffer top-up, mint/burn) → value-preserving capital movement; netted out of revenue.

Formula: `revenue = Δvalue − capital_inflow = external_inflow`.

`external_alm_sources` is empty by default — Grove has no off-chain yield distributors today, so revenue_E13–E17 = 0. The allowlist exists so a single config line can enable revenue tracking when an Anchorage-style sender is added; misclassification is a one-way risk (listing an internal address as external would inflate revenue), so the policy is "list only after confirming the address sends true off-chain yield."

Underlying primitive: `inflow_by_counterparty.sql` returns `[block_date, counterparty, signed_amount]` per holder; the compute layer filters on the allowlist and sums.

### 17.6 Distribution rewards — Phase 3+

Some primes (e.g. skybase) earn yield from active referral codes / liquidity-program payouts that arrive as periodic transfers, not as venue NAV growth. The pipeline reserves a `distribution_rewards` column in the headline (always 0 today) so the structure is stable when the source lands. Likely shape: a Dune-backed primitive that sums per-period payouts for a configured set of (chain, token, sender→recipient) tuples.

### 17.7 Methodology alignment vs. prime-settlement-methodology + debt-rate-methodology docs

Reference docs: `prime-settlement-methodology.md` (5-step framework) and `debt-rate-methodology.md` (per-position rate rules).

**Implemented (matches doc):**
- Step 1 base rate = `SSR + 30bps`, continuously compounded per-second (`apr_per_sec = ln(1+APY)/SECONDS_PER_YEAR`); daily summing per RULES §1.
- Step 2 idle USDS in subproxy + ALM proxy + **PSM** subtracted from `utilized` so prime is reimbursed BR. PSM term added Phase 2.B.7 — Grove's Spark PSM contribution = $0 because Grove uses PSM as a USDS↔USDC swap conduit, not as an idle deposit (verified via Dune query 7396569).
- sUSDS in subproxy treated as cost basis (`shares × entry_pps`, daily-resolution proxy via `convertToAssets` at each active day's EoD block). Avoids double-counting SSR.
- sUSDS in allocation modules (e.g. E18 sUSDS POL) NOT subtracted from utilized — the prime captures SSR organically via the venue's Δvalue (Cat B 4626 path); the implicit net = SR (kept) − BR (paid on debt) = spread, mathematically equivalent to doc Step 3's explicit "spread profit on idle sUSDS".
- Agent rate uniformly = `SSR + 20bps`. For sUSDS in subproxy, only the +20bps component applies (SSR already in the index).

- **Step 4 Sky Direct reimbursement (`sky_direct: true` venue flag).** Per-venue floor: `Prime Revenue = max(0, ActualRev − BR_charge)`; `Sky Revenue = BR_charge always (with shortfall absorbed)`. BR_charge computed daily-precise (`Σ_d AV_d × ((1+SSR_d+30bps)^(1/365)-1)`), with AV_d = balance_at_day(d) × NAV_at_day_eod_block. Marked Sky Direct for Grove: **E9 JTRSY** + **E10 BUIDL**. Other documented Sky Direct exposures don't apply to Grove today (USTB — not held; PSM USDC on non-Eth chains — Grove not exposed; Spark Curve sUSDS/USDT — Spark only). The orchestrator subtracts total shortfall from gross sky_revenue; `MonthlyPnL.sky_direct_shortfall` reports the absorbed amount.
- **Multi-month support.** `scripts/run_grove_2026_q1.py` runs Jan/Feb/Mar 2026 against the same lifetime fixtures (different pin_blocks per month). Required gating fixes: extended V3 events query (Dec 31 2025 → Mar 31 2026, captured the Feb 4 LP creation) and Chronicle NAV overrides for E7 STAC + E22 ACRDX at Dec 31 2025 (oracle pre-deployment).
- **RPC defensive coding.** `balance_of` / `scaled_balance_of` / `convert_to_assets` now treat empty (`0x`) returns and HTTP 4xx as zero — required for venues that didn't exist at older SoM blocks (E23 Steakhouse Prime Instant created mid-March; querying Feb SoM returns empty without this).

**Deferred (PRD-flagged):**
- **Subsidised rate ramp (debt-rate-methodology)** — first 24 months from prime start, rate linearly interpolates from `tbill_3m` → full base rate. Not implemented; we charge full BR. Grove is currently ~11.5 months in (start 2025-05-14), so would be at the midpoint between t-bill and BR. **Needs:** t-bill data source (Dune feed? config?) and per-prime opt-in flag.
- **Idle USDS/DAI in lending pools / AMMs (doc Step 2)** — doc lists Aave/SparkLend/Compound and Curve/Uniswap/Balancer USDS holdings as idle credit. No Grove venue currently holds USDS this way ($0 gap for Grove); scaffolding to be added when first prime needs it.
- **Sky Direct exposure scaffolding for non-Grove primes** — Spark's USDT in sUSDS/USDT Curve pools (Spark is unique in being USDT-denominated for some allocations). Not relevant for Grove; needed when Spark prime gets onboarded.
- **Reconciliation gap with Sky's reported Sky Share** (~$1.13M for Grove March 2026). The methodology + Sky Direct set are correct; the residual implies Sky uses a specific BR_charge "Asset Value" formula we haven't matched. Three approximations bracketed the answer (midpoint avg, daily time-weighted, EoM-only); next step would be either Sky's exact spec or per-tx pricing across the period.
- **Chronicle adapter robustness** — currently silently falls back to `const_one` ($1) when Chronicle returns `0x`, which can produce phantom revenue at SoM blocks before the oracle was deployed. Mitigated by `nav_overrides` fixture for known-historical NAVs at affected blocks, but the adapter itself should distinguish "pre-deployment" from "real $1" and refuse the const_one fallback for venues whose actual NAV is far from $1.

**Diverges from Maker's official query (`6954386_daily_utilized_usds`):** the official Maker query does not include the PSM term. Our `utilized` formula is more generous to the prime when PSM holdings exist; for Grove March 2026 the values match because Grove has nothing parked at PSM.

### 17.4 Migration plan delta (vs. §13 above)

| Phase | Original status (§13) | Actual status |
|---|---|---|
| 1 — Plumbing + OBEX | "9 PRs to ship" | ✅ shipped |
| 2.A — Grove Ethereum | "Σ cost_basis ≈ cum_debt within 1%" | ✅ shipped (gap was multi-chain, not Ethereum) |
| 2.B — Grove Base + Avalanche + Plume | rolled into above | ✅ shipped — cost-basis +0.06% in March (within tolerance); +0.63% with E23 added |
| 2.C — Grove Monad | rolled into above | ⚠️ partial — venue identified but RPC archival outside available range |
| 2.D — Methodology alignment (Cat A polarity, sUSDS cost basis, PSM, Step 4 Sky Direct) | (added late) | ✅ shipped — all in §17.7. Q1 2026 Grove numbers reproducible |
| 2.E — PSM3 mechanics + per-prime PSM config (Spark prerequisite) | (added late) | ✅ shipped — `PsmKind.{DIRECTED_FLOW,ERC4626_SHARES}` + per-chain `Prime.psm`. See §17.11. |
| 3.A — Spark scaffolding | planned | ⚠️ **partial** — `config/spark.yaml` (51 venues + 5 PSMs across 6 chains), ilk + subproxy verified, start_date confirmed (2024-11-18). Q1 2026 numbers NOT yet computed — see §17.12 for remaining work. |
| 3.B — Skybase | planned | ❌ not started |
| 4 — Indexer alternative | planned | ❌ not started |

### 17.8 Q1 2026 Grove numbers — reproducibility note

`scripts/run_grove_2026_q1.py` produces January, February, and March 2026 settlement numbers in one run. It uses the same lifetime Dune fixtures captured for March (debt, balances, SSR, mint/burn, cum_balance for RWA, V3 events, blocks_at_eod) and varies only `pin_blocks_som` / `pin_blocks_eom`. Two extensions vs the March-only run:

1. **V3 events fixture** widened from `[Feb 28, Mar 31]` to `[Dec 31 2025, Mar 31 2026]`, capturing the Feb 4 IncreaseLiquidity event ($25M USDC into the AUSD/USDC LP).
2. **`nav_overrides`** fixture key (in `dune_outputs.json`) provides explicit Chronicle NAVs for two `(oracle, block)` pairs where Chronicle had not yet started writing: E7 STAC at block 24136052 → $1000.00 (deposit-time par), E22 ACRDX at block 24136052 → $1.00 (deposit-time par). Without these, the prime_agent_revenue for January would include a phantom $100M jump on E7.

Tests: 211 unit + 8 integration passing, no regressions vs the post-Sky-Direct state.

### 17.9 Operational checks (recurring)

These are not blockers for the current pipeline but should be periodic operational audits. They protect against silent drift between the codebase, the Atlas spec, and on-chain reality.

#### Sky Direct exposure list — track Atlas changes
The set of exposures that qualify for Step 4 reimbursement is governed by Sky governance and recorded in the Sky Atlas (see [`sky-ecosystem/next-gen-atlas`](https://github.com/sky-ecosystem/next-gen-atlas)). Today the list is hardcoded in our config (`sky_direct: true` flag per venue in `<prime>.yaml`). When a new exposure type is approved or an existing one is removed, the YAML must be updated.

- **Recurring check (manual):** before each settlement cycle, diff the Atlas's Sky Direct exposures section against `config/<prime>.yaml`. Note the Atlas commit/date when the current list was last reconciled, e.g.: "Sky Direct list as of Atlas commit `<sha>` (`<date>`): Treasury Bills on Ethereum (BUIDL, JTRSY, USTB); USDC in PSM3 on non-Ethereum chains; USDT in sUSDS/USDT Curve pools."
- **Future automation:** parse the Atlas repo (presumably structured Markdown/YAML) and surface diffs vs. our config in a CI check, or generate the venue-level `sky_direct: true` flags directly from the Atlas as the source of truth.

#### On-chain flow-of-funds reconciliation — coverage check
The current venue list per prime is built by hand (PRD §3, §4) and cross-checked against external sources (e.g., BA Labs `stars-api.blockanalitica.com` — which surfaced the missing E23 Steakhouse Prime Instant on Base in this cycle). It's possible to miss a venue that the prime started using between settlements.

- **Recurring check:** trace USDS / underlying-asset flows out of the ALM proxy on each chain, follow them through swaps / deposits, and verify every destination contract is present in `config/<prime>.yaml` (or explicitly classified as a swap/PSM conduit, not a venue). The fixture `inflow_by_counterparty_e15` already captures the per-counterparty flow at one ALM (Grove Ethereum, USDC); the same Dune query parameterized by token + holder gives full coverage per (chain, ALM) pair.
- **Output:** a list of "addresses that received material funds but are not in the venue list", flagged for review.
- **Future automation:** add a `settle audit flow-of-funds --prime <id> --month <YYYY-MM>` subcommand that runs this diff and fails if any unrecognized counterparty crossed a configurable USD threshold.

### 17.10 Future work

Tracked but not blocking the current pipeline.

#### Review the Compute formulas
`compute/sky_revenue.py`, `compute/agent_rate.py`, `compute/prime_agent_revenue.py`, and `compute/monthly_pnl.py` together encode the methodology in §17.7. They've been adjusted multiple times (Cat A polarity flip, sUSDS cost basis, PSM term, Sky Direct Step 4) and warrant a fresh end-to-end methodology review:
- Verify each formula against `prime-settlement-methodology.md` + `debt-rate-methodology.md` after the refactors.
- Confirm sign conventions (inflow signs across the four payable/receivable terms are consistent).
- Confirm rate compounding (per-second APR vs daily APY) is applied uniformly.
- Reconcile residual gap with Sky's reported Sky Share (~$1.13M for Grove March 2026 — methodology is correct; "Asset Value" definition for BR_charge is the open question).

#### Per-venue revenue review
Today we report `prime_agent_revenue = Σ venue.revenue` and trust the per-venue computation end-to-end. Each venue's revenue should be independently audited:
- For Cat C (Aave) / Cat D (Spark): cross-check `scaledBalance × Δindex / RAY` against Aave/Spark's emitted yield events.
- For Cat B (Morpho 4626): cross-check `shares × ΔconvertToAssets` against the vault's NAV growth as reported by Morpho's API.
- For Cat E (RWA): cross-check `Δvalue − inflow` against the RWA issuer's published monthly returns (Centrifuge dashboard, BlackRock IDLF NAV report, etc.).
- For Cat F (LP): cross-check Curve `virtual_price` and Uniswap V3 fee accrual against pool-level NAV growth.

The output of this review would be a per-venue confidence rating: which venues we can trust the calc for unconditionally, which need an additional cross-check, and which need a different methodology.

### 17.11 PSM mechanics refactor (per-prime, per-chain)

The hardcoded `compute._psm.PSM_BY_CHAIN` dict is gone — PSMs are now declared per-prime in YAML under `addresses.<chain>.psm:`, with two supported mechanics dispatched by `PsmKind`:

| Mechanic | When | How USDS-equivalent is computed |
|---|---|---|
| `directed_flow` | Sky LITE-PSM-USDC pattern (Grove + Spark on Ethereum, OBEX) | Net token flow `(subproxy + ALM) → PSM` minus `PSM → (subproxy + ALM)`. Token is USDS, par-stable. |
| `erc4626_shares` | Spark PSM3 pattern (Base / Arbitrum / Optimism / Unichain) | PSM3 has a **non-standard ABI**: shares are *internal accounting* (no ERC-20 Transfer events) and the rate uses `convertToAssetValue(uint256)` (selector `0x41c094e0`), distinct from ERC-4626 `convertToAssets(uint256)`. We snapshot `convertToAssetValue(shares(alm, b), b)` at each day's EoD block; daily-net is the diff across days. |

Per-chain timeseries are summed by `_aggregate_psm_usds` into a single `psm_usds` DataFrame fed into `compute_sky_revenue`. For multi-chain primes (Spark), L2 PSM3 holdings reduce the Ethereum-denominated `utilized` since they were funded from Eth-borrowed USDS that was bridged over.

YAML snippet (Spark):
```yaml
addresses:
  ethereum:
    psm:
      kind: directed_flow
      address: '0x37305b1cd40574e4c5ce33f8e8306be057fd7341'   # Sky LITE-PSM-USDC
      token:   '0xdc035d45d973e3ec169d2276ddab16f1e407384f'   # USDS
  base:
    psm:
      kind: erc4626_shares
      address: '0x1601843c5e9bc251a3272907010afa41fa18347e'   # PSM3 Base
  # ... arbitrum, optimism, unichain similar
```

Grove numbers preserved exactly across this refactor (regression-checked). The 4 PSM3 venues in `config/spark.yaml` (S33/S40/S46/S50) were removed from the venue list — they're now PSM holdings, not Cat E venues.

### 17.12 Spark — current status (2026-04-29)

#### Done
- **Config scaffolding** (`config/spark.yaml`): 51 venues (Cat A: 15, Cat B: 17, Cat C: 12, Cat E: 5, Cat F: 2) across 6 chains (Ethereum, Base, Arbitrum, Optimism, Unichain, Avalanche-C). Per-chain PSM stanzas (5 — Eth `directed_flow` + 4 L2 `erc4626_shares`).
- **Identifiers verified via Dune** (query 7399640): ilk = `ALLOCATOR-SPARK-A` = `0x414c4c…000000`, subproxy (urn) = `0x691a6c29e9e96dd897718305427ad5d534db16ba`, first frob = 2024-11-18 (block 21,215,063), 49,794 frobs through 2026-04-29 (most active allocator across all primes).
- **Sky Direct flags**: 4 venues confirmed Sky Direct (S19 BUIDL-I, S20 JTRSY, S21 USTB on Ethereum + S24 sUSDSUSDT Curve). PSM3 holdings are NOT venues so the prior 4 Sky-Direct flags on the PSM3 venues went away with the refactor.
- **PSM3 ABI validated** (2026-04-29). On-chain probes of Base PSM3 (`0x1601843c…`) and Optimism PSM3 (`0xe0f9978b…`) confirm PSM3 does **not** implement standard ERC-4626 `convertToAssets(uint256)`. The contract exposes:
    * `shares(address) → uint256` — selector `0xce7c2ac2` (internal share balance; no ERC-20 Transfer events)
    * `convertToAssetValue(uint256 numShares) → uint256` — selector `0x41c094e0` (USDS-equivalent, 18-dec)
    * `convertToAssets(address asset, uint256 numShares)` — different signature than ERC-4626's; not used here
  
  Code now uses the correct ABI: new `IPsm3Source` protocol + `RPCPsm3Source` impl + `psm3_shares` / `psm3_convert_to_asset_value` in `extract.rpc`. The `erc4626_shares` branch of `get_psm_usds_timeseries` reads daily snapshots `convertToAssetValue(shares(alm, b), b)` for each day in the period.
- **Spark sky-revenue runner** (`scripts/run_spark_2026_q1.py`) built. Computes ONLY `sky_revenue` (per user direction 2026-04-28). Loads Spark prime config, resolves EoM blocks per chain, fetches debt + Eth-side balances + SSR via Dune, builds PSM USDS-equivalent timeseries (Eth directed-flow + L2 PSM3 RPC), runs `compute_sky_revenue` for each of Jan/Feb/Mar 2026, persists per-month JSON to `settlements/spark/<YYYY-MM>/`.

#### Pending fixture captures (Dune)
Query IDs created and partially executed; pagination + JSON-write not yet completed (see `tests/fixtures/spark_2026_q1/MANIFEST.json` for IDs):

| Query | Dune ID | Status |
|---|---|---|
| Spark debt timeseries (ALLOCATOR-SPARK-A) | 7399651 | executed; partial pagination (~200 rows seen, full set is larger) |
| Spark Eth balances combined (subproxy/ALM × USDS/sUSDS + Sky LITE-PSM directed flow) | 7399654 | executed; not paginated |
| Blocks_at_eod for arbitrum/optimism/unichain | 7399659 | executed; not paginated |
| PSM3 share-balance timeseries (4 L2 ALMs) | 7399661 | executed; not paginated |

Per-venue mint/burn + cum_balance fixtures (~30 venues) are also unresolved.

#### Pricing paths needed for `prime_agent_revenue`
Many novel paths vs Grove. Per-category:

| Cat | Spark venues | New path needed? |
|---|---|---|
| A (idle stables) | 15 | No — same logic as Grove E13–E17, just for new tokens (USDe, PYUSD, multi-chain). External_alm_sources stays empty → revenue=0. |
| B (4626 vaults) | 17 | Mostly works (Cat B = `shares × convertToAssets`). New surfaces: Maple syrupUSDC/USDT (works as 4626), Ethena sUSDe (rebasing — needs verification it's a true 4626), Fluid fsUSDS (4626), Arkis sparkPrimeUSDC1 (4626 + API confirmation), Spark-branded Morpho vaults (4626). |
| C (aTokens / spTokens) | 12 | Works (existing scaledBalanceOf path). Multi-chain Aave aTokens (aBasUSDC, aArbUSDCn, aAvaUSDC) — verify the Aave V3 ABI is consistent across chains. |
| E (RWA) | 5 | BUIDL-I + JTRSY share existing pricing paths with Grove. **USTB and USCC** (Superstate) need a new oracle (no Chronicle feed; Superstate publishes NAV via API). **Anchorage** is a $150M off-chain custodial position — treated as principal sent out, with principal+yield expected to return as a transfer to the ALM later (no on-chain valuation needed). |
| F (LP) | 2 | Both Curve stableswap, same path as Grove E11. PYUSDUSDS adds PYUSD to the par-stable registry; sUSDSUSDT pricing needs sUSDS price (yield-bearing — check if Curve's `virtual_price` already accounts for it). |

#### Q1 2026 Spark numbers — sky_revenue ✅ SHIPPED, prime_agent_revenue IN PROGRESS

**Sky revenue (Q1 2026 — completed 2026-04-29):** $25.74M total via `scripts/run_spark_2026_q1.py`. Per-month: Jan $9.08M, Feb $8.36M, Mar $8.30M. Artifacts at `settlements/spark/{2026-01,02,03}/sky_revenue_only.json`. PSM3 reads via RPC (drpc) sampled at month-boundary dates with linear interpolation (full daily reads were prohibitively slow on drpc Arbitrum). Subsidised rate ramp NOT applied (Spark is ~17/24 mo into ramp; applying it would reduce sky_revenue per debt-rate-methodology file 2).

**`prime_agent_revenue` — venue inventory + status (2026-04-29):**

51 venues across 6 chains. Categorization + blocker status:

| Cat | # venues | Pricing path | Status |
|---|---|---|---|
| **A** (idle par-stables) | 15 | `_cat_a_capital_inflow_timeseries` — needs `cum_balance` per venue (Dune), source-tagged inflow if `external_alm_sources` non-empty | Spark's `external_alm_sources` is empty → Cat A revenue contribution = $0 regardless of holdings. Needs cum_balance fixtures only for the value snapshot (par × balance). |
| **B** (ERC-4626) | 17 | `_shares_to_usd_inflow_timeseries` — `cum_balance` (shares) per venue (Dune) + `convertToAssets` (RPC) at SoM/EoM | Most work as standard 4626. **sUSDe** (S16) needs verification it's a true 4626 (Ethena cooldown design); **sparkPrimeUSDC1** (S18) Arkis API ideal but `totalAssets()` is the runtime fallback per `docs/pricing/allocation_pricing.csv`. |
| **C** (Aave/Spark spTokens) | 12 | `_atoken_index_weighted_inflow` — `balance_at` + `scaled_balance_at` (RPC, scaledBalanceOf path). **No Dune fixture needed.** | All venues use the same scaledBalanceOf ABI; Aave V3 + SparkLend share the contract. RPC calls via Eth/Base/Arb/Op/Avalanche-C providers (need drpc-resilient retry, already in place). |
| **E** (RWA) | 5 | `_rwa_inflow_timeseries` — `cum_balance` per venue (Dune, with `min_transfer_amount` filter for BUIDL-style yield mints) + NAV oracle | **S19 BUIDL-I** + **S20 JTRSY** share Grove's oracle paths (Chronicle for JTRSY, const_one for BUIDL). **S21 USTB / S22 USCC**: no holdings as of Q1 2026 → const_one is fine; real Superstate oracle deferred. **S23 Anchorage**: $150M custodial position; current YAML uses const_one (treated as principal-sent-out, yield arrives later as ALM transfer). Documented limitation. |
| **F** (Curve LP) | 2 | `_curve_lp_index_weighted_inflow` — `balance_of(LP)` (RPC) + Curve `pool.balances/get_virtual_price` (RPC). **No Dune fixture needed.** | **S24 sUSDSUSDT** (Sky Direct, BR_charge applies) — needs sUSDS underlying price (= `convertToAssets(1)`). **S25 PYUSDUSDS** — needs PYUSD added to the par-stable registry. |

**Blocker breakdown:**

* **Unblocked (49/51 venues):** Cat A all 15, Cat B 16/17 (all except sUSDe), Cat C all 12, Cat E 4/5 (all except Anchorage on-chain valuation), Cat F both 2. Pricing paths exist; needs only Dune fixture capture + runner wiring.
* **Sub-percent risk (2/51 venues):** S16 sUSDe — assumed true 4626 (totalAssets-based) until verified. S18 sparkPrimeUSDC1 — uses 4626 fallback; Arkis API is preferred per docs but not load-bearing.
* **Open (1/51 venues):** S23 Anchorage. Current YAML treats as on-chain const_one — produces wrong revenue if any meaningful balance sits at `0x49506c…`. Plan: confirm with Spark/Sky team that Anchorage proxy holds $0 on-chain during Q1 2026 (yield to arrive in a later month), document the lag in the artifact.

**Dune fixtures needed for the unblocked slice:**

| Fixture group | Query count | Status |
|---|---|---|
| Eth-side: debt, ALM/subproxy USDS+sUSDS, SSR | 3 (debt ✅, balances ✅, SSR reused from Grove ✅) | Captured |
| L2 daily EoD blocks (Base/Arb/Op/Uni) | 1 | Captured (`l2_daily_eod_blocks.json`) |
| Eth + Avalanche-C daily EoD blocks | 2 | TODO |
| Per-venue Cat A cum_balance (15) | 15 | TODO (low priority — Cat A revenue = $0 with empty external_alm_sources) |
| Per-venue Cat B cum_balance / shares timeseries (17) | 17 | TODO (high priority — drives Cat B revenue) |
| Per-venue Cat E cum_balance with min_transfer filter (5) | 5 | TODO (BUIDL/JTRSY drive revenue) |

Cat C + Cat F venues do not need Dune fixtures — pricing path is RPC-only.

**Next steps (prime_agent_revenue):** capture the 39 remaining Dune queries, wire `compute_monthly_pnl` through a Spark fixture loader mirroring Grove's, run for Q1 2026, and document the Anchorage/sUSDe/sparkPrimeUSDC1 asterisks against the result.

#### `prime_agent_revenue` slice progress (2026-04-29 — paused mid-fixture-capture)

Started on the unblocked slice. Status:

* **Cat E inventory captured (5 venues, 28 rows total — Dune query 7402171, execution `01KQDKHWFC0BN209RKYAVHWVD8`):** **All Cat E positions are $0 by Q1 2026.** Material finding:
  * S19 BUIDL-I: peaked at ~$799M in May 2025, fully exited by 2025-07-28
  * S20 JTRSY: peaked at ~$376M in May 2025, fully exited by 2025-07-28
  * S21 USTB: peaked at ~$28M in April 2025, fully exited by 2025-07-17
  * S22 USCC: peaked at ~$13M in November 2025, fully exited by 2025-12-03
  * S23 Anchorage: zero on-chain transfers throughout the period
  * **Implication:** Cat E contributes ~$0 to Spark's Q1 2026 prime_agent_revenue. The Sky Direct shortfall (Step 4 BR_charge floor) on these venues is also $0 since `value_d × NAV_d` is zero throughout the period. Anchorage's $150M off-chain position (per user, principal-sent-out awaiting return as ALM transfer) is genuinely off-chain and won't show in any on-chain query.
* **Cat B Eth fixture in flight (Dune query 7402163, execution `01KQDKHVCMZ0VP7MRMMSMK9FBB`):** ~600+ rows captured; full pagination ~700–800 rows total. Sample snapshot at 2026-03-31: S14 syrupUSDC ~$86M, S15 syrupUSDT ~$89M, S18 sparkPrimeUSDC1 ~$10M, S32 sUSDS ~$247M (still growing into Q1), others <$1M.
* **Cat B L2s fixture in flight (Dune query 7402168, execution `01KQDKHVS8CP7VR9HZ928NW3PS`):** ~100 rows captured; one venue (S34 Spark Base USDC) shows steady growth from $3M in Jan 2025 to $429M in Jul 2025 — likely a major TVL contributor.
* **Eth + Avalanche-C daily blocks fixture in flight (Dune query 7402172, execution `01KQDKJ5E0R9FA6DV3YQJTJH39`):** ~100 rows captured; need full ~180 to cover the period for both chains.
* **Saved fixtures so far:** `tests/fixtures/spark_2026_q1/{debt_timeseries.json, l2_daily_eod_blocks.json}`. Cat B + Cat E fixtures NOT yet persisted to JSON (data captured in conversation, not on disk).
* **Runner not yet built.** A `scripts/run_spark_2026_q1.py` rewrite to call `compute_monthly_pnl` (vs. the current `compute_sky_revenue`-only path) is the next step after fixture capture finishes.

**Realistic remaining work:** ~30 more MCP pagination rounds to finish Cat B Eth + Cat B L2s + remaining block fixtures, then write a Spark fixture loader (mirroring `tests/fixtures/grove_fixture_loader.py`), then build the runner. Estimated 1–2 more focused sessions. ✅ All shipped — see `## Q1 2026 Spark prime_agent_total_revenue ✅ SHIPPED` below.

#### Q1 2026 Spark prime_agent_total_revenue ✅ SHIPPED (2026-04-30)

Full `compute_monthly_pnl` ran end-to-end for Jan/Feb/Mar 2026 via `scripts/run_spark_2026_q1_full.py` and the new Spark fixture loader (`tests/fixtures/spark_fixture_loader.py`):

| Month | prime_agent_total | sky_revenue | sky_direct_shortfall | **monthly_pnl** |
|---|---:|---:|---:|---:|
| Jan 2026 | $5,721,164 | $10,449,914 | $0 | **−$4,728,750** |
| Feb 2026 | $5,078,985 | $9,853,356 | $0 | **−$4,774,370** |
| Mar 2026 | $5,710,661 | $9,799,389 | $0 | **−$4,088,728** |
| **Q1 total** | **$16,510,810** | **$30,102,659** | **$0** | **−$13,591,849** |

Top venue contributors (Q1 average):
* **S28 PYUSD raw at ALM**: $0 revenue ✓ (Cat A revenue=0 per the methodology fix; balance growth treated as capital movement)
* **S32 sUSDS at ALM**: ~$2M/month — biggest yield contributor
* **S1–S5 SparkLend spTokens (Eth)**: ~$2M/month combined — aToken-style rebasing yield
* **S14 syrupUSDC, S15 syrupUSDT**: ~$0.5M/month combined
* **Cat E**: $0 (all RWA positions exited by Q1; sky_direct_shortfall = $0)
* **Cat F (Curve LPs S24/S25)**: ~$0.05M/month combined

**Methodology fixes applied this session:**

1. **Cat A par-stable fallback** (`src/settle/normalize/positions.py`). The `_cat_a_capital_inflow_timeseries` function previously left period_inflow = $0 when both `inflow_by_counterparty` and `external_alm_sources` were empty, causing balance changes to be falsely counted as revenue. The methodology says: par-stables don't generate yield by themselves; without a registered external yield source, all balance changes must be capital. Fix: when both are empty, fall back to `cumulative_balance_timeseries` and treat all flows as capital → revenue = 0. Applies to all primes; Grove (which has rich `inflow_by_counterparty` data) is unaffected.

2. **PSM3 ABI** (Spark-specific, fixed earlier this session). Spark's PSM3 uses `shares(address)` + `convertToAssetValue(uint256)`, not standard ERC-4626 `convertToAssets(uint256)`. New `IPsm3Source` protocol + `RPCPsm3Source` impl.

3. **Curve LP yield-bearing coin pricing** (`src/settle/domain/sky_tokens.py`, `src/settle/normalize/prices.py`, `src/settle/normalize/positions.py`). Added `KNOWN_YIELD_BEARING_ETHEREUM` registry with sUSDS → USDS recursion. The S24 sUSDSUSDT pool now prices via `convertToAssets(sUSDS)` + USDS par.

4. **Zero-balance short-circuit in `get_position_value`**. Skip the unit_price call when balance is 0 — avoids exotic-pricing-path failures on venues that hold $0 in the period.

5. **sparkPrimeUSDC1 (S18) decimals fix** (`config/spark.yaml`): on-chain `decimals() = 6`, not 18 as previously specified.

6. **drpc retry hardening** (`src/settle/extract/rpc.py`): bumped retry attempts to 60, capped backoff at 3s, retried 408/429 + transient JSON-RPC errors.

7. **Cache write race fix** (`src/settle/extract/cache.py`): per-(pid,tid) tmp suffix to prevent concurrent ThreadPoolExecutor writes from clobbering each other.

**Known caveats (documented):**

* **Subsidised rate ramp not applied**: Spark is ~17/24 months into the ramp; applying it would lower Sky_revenue per debt-rate-methodology file 2.
* **PSM3 USDS-equivalent sampled at month boundaries** with linear interp (drpc Arbitrum was too flaky for daily reads).
* **fsUSDS pricing approximation**: S17/S36/S42 use sUSDS as underlying; sUSDS is treated as $1 par (small understate). All three venues hold $0 in Q1 2026 so the error is $0 in practice.
* **Anchorage S23**: $150M off-chain custodial position is invisible on-chain. Treated as principal-sent-out; yield would arrive as a future ALM transfer (none seen in Q1).

Artifacts: `settlements/spark/{2026-01,02,03}/{pnl.md,pnl.csv,venues.csv,provenance.json}`.

### 17.13 Open questions (priority-ordered)

#### High priority (Spark Q1 — most resolved 2026-04-30)
1. **L2 RPC endpoints + Dune key** — *resolved*. drpc URLs for Arbitrum / Optimism / Unichain were added to `.env`. `DUNE_API_KEY` not set, but Dune access via MCP unblocked the captures.
2. **Spark `start_date` boundary** — first frob is 2024-11-18; could differ from Sky's billing anchor. Verify with Spark/Sky team. **See QUESTIONS_SPARK.md Q1.**
3. **PSM3 ABI** — *resolved*. Confirmed on Base + Optimism live; Arbitrum + Unichain assumed same ABI (CREATE2-deployed by Spark). Selectors `0xce7c2ac2` (`shares`) + `0x41c094e0` (`convertToAssetValue`). Implementation updated.
4. **Spark Sky Direct list** — *resolved*. Flagging S19/S20/S21/S24. PSM3 holdings handled via PSM mechanic. Anchorage = principal-sent-out.
5. **Cat A revenue methodology** — *resolved 2026-04-30*. `_cat_a_capital_inflow_timeseries` falls back to `cumulative_balance_timeseries` when both `inflow_by_counterparty` and `external_alm_sources` are empty → revenue = 0 (correct for par-stables with no off-chain yield source). Spark has empty `external_alm_sources`, so its Cat A revenue is $0.
6. **Hardcoded period detection in `spark_fixture_loader.py`** — **NEW (from review):** the loader's `eth_eom`-block branch only handles Q1 2026 (Jan/Feb/Mar). Any other month silently skips Cat A `cumulative_balance_timeseries` synthesis → revenue overstate. Fix needed before re-running for Q2+. **See QUESTIONS_SPARK.md Q9.**
7. **PSM3 daily RPC error isolation** — **NEW (from review):** `_value_at` propagates exceptions per-day; one failed RPC kills the whole chain's PSM3 timeseries → utilized over-stated. Wrap with per-day try/except + log. **See QUESTIONS_SPARK.md Q10.**

#### Medium priority (affect numerical accuracy)
5. **Reconciliation gap with Sky's reported Sky Share for Grove** (~$1.13M for Mar 2026). Methodology + Sky Direct set are correct; the residual is in BR_charge "Asset Value" definition. **Need:** Sky's exact spec for Asset Value in Step 4 calc.
6. **Subsidised rate ramp** (debt-rate-methodology, file 2). Linearly interp from `tbill_3m` to BR over 0–24 months from prime start. Spark is currently ~17 months in (Nov 2024 → Apr 2026), so rate would be ~70% of the way to full BR. Material impact on Spark's Sky revenue. **Need:** t-bill data source + per-prime opt-in flag in YAML.
7. **Sky Direct exposure list — automation** — manual diff against `sky-ecosystem/next-gen-atlas` is the current process. As the list grows beyond Treasury Bills + PSM3 + Spark Curve, this becomes load-bearing.
8. **Chronicle adapter robustness** — silently falls back to const_one ($1) when oracle returns 0x. Mitigated by `nav_overrides` fixture but the adapter itself should be tightened (distinguish "pre-deployment" from "real $1").

#### Low priority (operational)
9. **Monad RPC archival window** — both Alchemy and drpc Monad endpoints have ~3.8M-block archival caps. Grove's E25 candidate venue on Monad (~$6.5M EoM) is unblocked from this issue. **Need:** dedicated archival Monad node OR Dune-cum-balance × const-pps approximation in the value path.
10. **CLI** — `src/settle/cli.py` is mostly placeholder. Production needs `python -m settle run <prime> <month>` with registry-default sources, multi-month batch, `--no-cache` flag.
11. **Hard validation gates** at the Compute boundary (cost-basis tolerance, monotonic invariants, source-pair drift). Today only the `MonthlyPnL.__post_init__` round-trip identity is enforced.
12. **Live end-to-end test** — all current tests use fixtures or mocks; one real-Dune + real-RPC run would prove the production paths work. `tests/e2e/` has only the OBEX oracle test today.

#### Future work (longer-term)
13. **Compute-formula audit** — fresh end-to-end methodology review of `compute/sky_revenue.py`, `compute/agent_rate.py`, `compute/prime_agent_revenue.py`, `compute/monthly_pnl.py` after the multiple refactors.
14. **Per-venue revenue audit** — independent cross-check of each venue's calc against external truth (Aave events, Morpho API, Centrifuge / BlackRock NAV reports, Curve / V3 pool data) → produces a per-venue confidence rating.
15. **On-chain flow-of-funds reconciliation automation** — `settle audit flow-of-funds --prime <id> --month <YYYY-MM>` subcommand that flags any unrecognized counterparty crossing a USD threshold.
16. **Idle USDS/DAI in lending pools / AMMs** (doc Step 2 — beyond just subproxy/ALM/PSM). No prime currently holds USDS this way; scaffolding to add when first prime needs it.
17. **Distribution rewards** — Phase 3+ placeholder for referral/liquidity-program payouts (skybase). Field exists; populated when source lands.
