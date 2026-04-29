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

**As of 2026-04-29.** This section is updated as the implementation progresses; everything above is the original design and is preserved for reference.

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
| 3 — Spark + Skybase | planned | ❌ not started |
| 4 — Indexer alternative | planned | ❌ not started |

### 17.8 Q1 2026 Grove numbers — reproducibility note

`scripts/run_grove_2026_q1.py` produces January, February, and March 2026 settlement numbers in one run. It uses the same lifetime Dune fixtures captured for March (debt, balances, SSR, mint/burn, cum_balance for RWA, V3 events, blocks_at_eod) and varies only `pin_blocks_som` / `pin_blocks_eom`. Two extensions vs the March-only run:

1. **V3 events fixture** widened from `[Feb 28, Mar 31]` to `[Dec 31 2025, Mar 31 2026]`, capturing the Feb 4 IncreaseLiquidity event ($25M USDC into the AUSD/USDC LP).
2. **`nav_overrides`** fixture key (in `dune_outputs.json`) provides explicit Chronicle NAVs for two `(oracle, block)` pairs where Chronicle had not yet started writing: E7 STAC at block 24136052 → $1000.00 (deposit-time par), E22 ACRDX at block 24136052 → $1.00 (deposit-time par). Without these, the prime_agent_revenue for January would include a phantom $100M jump on E7.

Tests: 192 unit + 8 integration passing, no regressions vs the post-Sky-Direct state.
