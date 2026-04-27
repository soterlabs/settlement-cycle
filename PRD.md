# PRD — `settlement-cycle`: MSC monthly settlement pipeline

**Status:** Draft (kickoff)
**Owner:** lakonema2000
**Created:** 2026-04-27
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
