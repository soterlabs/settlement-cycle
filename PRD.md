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

This PRD is the kickoff for the implementation. The deliverable is the Python package in this repo (`settlement-cycle`) that replaces the monolithic Dune query approach with a 4-stage Extract / Normalize / Compute / Load pipeline. The existing OBEX Dune query is preserved in [`reference/obex_monthly_pnl.sql`](reference/obex_monthly_pnl.sql) as the historical reconciliation oracle. Its e2e test was retired 2026-09-01 — see §17.13: the rate methodology changed and past settlements are not restated, so a query on the old convention can no longer be a parity target.

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
def get_debt_timeseries(
    prime: Prime,
    period: Period,
    *,
    source: IDebtSource | None = None,
    block_resolver: IBlockResolver | None = None,  # production path scales Art × rate daily
) -> pd.DataFrame: ...

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
│   │   ├── <prime>_<month>/          ← per-prime capture dirs
│   │   └── grove_2026_03/
│   ├── unit/
│   │   ├── test_compute_sky_revenue.py
│   │   ├── test_compute_agent_rate.py
│   │   └── test_compute_monthly_pnl.py
│   ├── integration/
│   │   └── test_normalize_with_mock_sources.py
│   └── e2e/
│       └── (e2e oracle test retired 2026-09-01 — see §17.13)
├── docs/                               ← design + per-prime context
│   ├── RULES.md
│   ├── SETTLEMENT_ARCHITECTURE.md, ASSET_CATALOG.md, VALUATION_METHODOLOGY.md
│   ├── ALM_COUNTERPARTIES.md
│   ├── valuation_poc/                  ← Dune↔Python POC (COMPARISON, QUESTIONS)
│   ├── obex/                           ← OBEX README + monthly findings/
│   ├── grove/                          ← Grove PRD/README/QUESTIONS (Phase 2)
│   └── {keel,prysm,skybase,spark}/     ← Phase 3+ prime READMEs
├── reference/
│   └── obex_monthly_pnl.sql            ← historical reference implementation
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
- **Fixture-based replay tests**: freeze Extract outputs under `tests/fixtures/<prime>_<month>/`; replay through Normalize → Compute → Load; assert the artifact matches a committed expected result. (The original `obex_2026_03` oracle capture was retired 2026-09-01 — see §17.13.)
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
9. **E2E test** — `(retired — see §17.13)`. CI gate.

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
- ✅ Multi-month 2026 run (`scripts/run_grove_2026.py`) produces Jan / Feb / Mar / Apr / May settlement numbers in one execution. See §17.8.

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

### 17.3 Headline numbers — Grove Q1 2026 (post 2026-05-02 reconciliation)

The reported headline is `prime_agent_total_revenue` (what the prime is owed) and `sky_revenue` (what the prime owes Sky), reported **separately** — they're not netted at this layer. The previously-emitted `monthly_pnl = prime_agent_revenue + agent_rate + distribution_rewards − sky_revenue` is kept for audit (`provenance.json`) but no longer displayed in the markdown headline or `pnl.csv`.

After the 2026-05-02 refactors (subsidised rate, SDE config table with capped JAAA, Net-Subs base, Centrifuge `pricePerShareFeed`, E2 dust fix, Avalanche venues skipped), the Q1 2026 numbers are:

| Month | prime_agent_total_revenue | sky_revenue | sky_direct_shortfall (legacy) | monthly_pnl |
|---|---:|---:|---:|---:|
| 2026-01 | $3,294,342 | $5,825,250 | $0 | −$2,524,644 |
| 2026-02 |   $865,528 | $5,640,013 | $0 | −$4,768,685 |
| 2026-03 |   $129,496 | $6,302,405 | $0 | −$6,166,635 |
| **Q1 total** | **$4,289,366** | **$17,767,668** | **$0** | **−$13,459,964** |

Reproduce: `python3 scripts/run_grove_2026.py` (lifetime Dune fixtures cover all three Q1 months; pin blocks per month are hardcoded in `PIN_BLOCKS_BY_MONTH`).

`sky_direct_shortfall` is now always 0 — under the new SDE-split model Sky takes the actual venue revenue (no more floor / shortfall absorption). The legacy field is preserved for provenance round-trip on settlements written under the old model.

**Reconciliation vs Grove team's Feb 2026 PnL workbook (`data/grove/feb_2026.xlsx`):**

| | Grove team | Ours | Diff |
|---|---:|---:|---:|
| JAAA_ETH actual_revenue | $1,388,581 | $1,388,517 | −$64 |
| JTRSY_ETH actual_revenue | $1,437,927 | $1,437,959 | +$32 |
| **Total Profit to Sky** | **$6,042,238** | **$5,640,013** | **−$402,225** |

The $402K residual is dominated by **E1 aHorRwaRLUSD off-pool yield** (~$430K/month) — see §17.13 high-priority entry: Grove team accrues this off-chain, no equivalent feed in our pipeline yet.

`distribution_rewards` is the prime's Distribution Rewards (referral / liquidity-program payouts). **Sourced as of 2026-06** from the `settle-dr-dune` submodule's reconciliation workbook (Summary tab, per ref code) for Spark / Grove / Skybase / Keel — see §17.6. Field flows through to all output formats; primes/months with no DR source render 0.00.

### 17.5 Cat A inflow accounting — design note

Idle par-stable holdings on the ALM (E13–E18 for Grove) use **per-counterparty source tagging via an external allowlist**. Every Transfer to/from the ALM is classified by counterparty:

- Counterparty in `prime.external_alm_sources[chain]` → off-chain custodian sending realized yield (e.g. Anchorage). Inflow passes through to revenue.
- Any other counterparty (PSM swap leg, venue contract allocation/withdrawal, AllocatorBuffer top-up, mint/burn) → value-preserving capital movement; netted out of revenue.

Formula: `revenue = Δvalue − capital_inflow = external_inflow`.

`external_alm_sources` is empty by default — Grove has no off-chain yield distributors today, so revenue_E13–E17 = 0. The allowlist exists so a single config line can enable revenue tracking when an Anchorage-style sender is added; misclassification is a one-way risk (listing an internal address as external would inflate revenue), so the policy is "list only after confirming the address sends true off-chain yield."

Underlying primitive: `inflow_by_counterparty.sql` returns `[block_date, counterparty, signed_amount]` per holder; the compute layer filters on the allowlist and sums.

### 17.6 Distribution rewards — sourced from `settle-dr-dune` (2026-06)

Some primes earn yield from referral codes / liquidity-program payouts that arrive as periodic transfers, not as venue NAV growth. These **Distribution Rewards (DR)** are now sourced from the [`settle-dr-dune`](https://github.com/soterlabs/settle-dr-dune) submodule — a transparent on-chain reconstruction of DR revenue by ref code (a self-owned alternative to Spark's opaque `dune.sparkdotfi.result_spark_*` datasets).

Integration (`src/settle/load/dr_rewards.py`): the `Summary` tab of `dune-results/dr_comparison_latest.xlsx` is grouped by prime, with one DR-USD column per month and a per-group `Total`. `enrich_with_dr` populates `MonthlyPnL.distribution_rewards` (the group total for the period) + `dr_breakdown` (per-ref-code rows) at report-write time; `summary.md` shows the headline figure + a "DR per ref code" table.

Scope: tagged-DR primes **Spark / Grove / Skybase / Keel**. The untagged "Other" bucket and primes without a DR group (e.g. obex) are excluded (render 0.00). Refresh without recompute via `python scripts/run_{prime}_2026.py --dr-only` (patches existing provenance + re-renders; no RPC / Dune).

### 17.7 Methodology alignment vs. prime-settlement-methodology + debt-rate-methodology docs

Reference docs: `prime-settlement-methodology.md` (5-step framework) and `debt-rate-methodology.md` (per-position rate rules).

**Implemented (matches doc):**
- Step 1 base rate = `SSR + 30bps`, continuously compounded per-second (`apr_per_sec = ln(1+APY)/SECONDS_PER_YEAR`); daily summing per RULES §1.
- Step 2 idle USDS in ALM proxy + **PSM** subtracted from `utilized` so prime is reimbursed BR. PSM term added Phase 2.B.7. **No Ethereum PSM is tracked** — the mainnet PSM stack (LITE-PSM `DssLitePsm` + USDC pocket EOA + `DaiUsds` converter + retail `UsdsPsmWrapper`) is non-custodial; every prime↔PSM interaction completes atomically in a single swap tx, no balances accumulate. Verified on-chain via a Spark trace (Dune tx `0x2c81d2de…42c0b6`): USDS → DAI (via DaiUsds mint/burn) → USDC (`DssLitePsm.buyGem` transfers USDC from pocket to ALM). An earlier `directed_flow` PsmKind probed USDS flow at the pocket — removed 2026-05-11 (see §17.11) because USDS never touches the pocket; the pocket only holds USDC. Only the L2 **PSM3** (custodial, share-based) is tracked. Subproxy USDS/sUSDS are NOT subtracted from utilized — the subproxy holds a mix of genesis capital, treasury holdings, risk capital, and realized revenue that does not all correspond to ilk debt; these balances earn the agent rate instead.
- **Step 2 Curve pool idle USDS** subtracted from `utilized` via `curve_idle_usds` venue config, **par-stable coins only**. For each configured venue the prime's proportional share of the coin reserve is computed daily via RPC (`read_pool` + `balanceOf`). Enabled for Spark S25 (USDS leg of PYUSD/USDS pool) at $1/unit. S24 (sUSDS leg) is tracked in the pipeline for future Prime Revenue use but does **not** reduce `utilized` — subtracting a yield-bearing balance as USDS-equivalent is incorrect.
- **Step 2 Lending pool idle underlying** subtracted from `utilized` via `lending_idle_usds: true` venue flag (Cat C/D only). For each configured spToken/aToken venue the prime's proportional share of the unborrowed underlying is computed daily via RPC: `(balanceOf(ALM, spToken) / totalSupply(spToken)) × balanceOf(spToken_contract, underlying)`. Enabled for Spark S1 (spUSDS/USDS) and S4 (spDAI/DAI — par-stable at $1). `utilized = cum_debt − ALM − PSM − Σ curve_idle_usds − Σ lending_idle_usds`.
- sUSDS in subproxy treated as cost basis (`shares × entry_pps`, daily-resolution proxy via `convertToAssets` at each active day's EoD block). Avoids double-counting SSR.
- sUSDS in allocation modules (e.g. the L2 sUSDS POL proxies S37/S43/S47/S51) is **not** deducted from `utilized`. Prime Revenue for these venues is **30 bps spread only** (BR − SSR), not the full SSR appreciation. (S32 is the `demand_side_spread` special case — its 30bps is routed to Spark Savings depositors via DSDR and the prime gets no supply-side spread; see §17.13 Spark item 9.) Crediting the full SSR appreciation as Prime Revenue would double-count: the prime already receives SSR through the sUSDS share price, so an additional model credit would yield `(2×SSR − BR) × V > 0` — an overcredit of ~3.7%/yr — when the intent is economic neutrality (net = 0). Implementation: Cat B yield-bearing venues set `actual_revenue_override = value_som × 30bps_daily × n_days` in `VenueRevenueInputs`. See `docs/METHODOLOGY.md §1` and `docs/RULES.md Rule 5`.
- Agent rate uniformly = `SSR + 20bps`. For sUSDS in subproxy, only the +20bps component applies (SSR already in the index).

- **Step 4 Sky Direct reimbursement (`sky_direct: true` venue flag).** Per-venue floor: `Prime Revenue = max(0, ActualRev − BR_charge)`; `Sky Revenue = BR_charge always (with shortfall absorbed)`. BR_charge computed daily-precise (`Σ_d AV_d × ((1+SSR_d+30bps)^(1/365)-1)`), with AV_d = balance_at_day(d) × NAV_at_day_eod_block. Marked Sky Direct for Grove: **E9 JTRSY** + **E10 BUIDL**. Other documented Sky Direct exposures don't apply to Grove today (USTB — not held; PSM USDC on non-Eth chains — Grove not exposed; Spark Curve sUSDS/USDT — Spark only). The orchestrator subtracts total shortfall from gross sky_revenue; `MonthlyPnL.sky_direct_shortfall` reports the absorbed amount.
- **Multi-month support.** `scripts/run_grove_2026.py` runs Jan / Feb / Mar / Apr / May 2026 in a single execution, switching fixture dirs between Q1 (`grove_2026_03`), Apr (`grove_2026_04`) and May (`grove_2026_05`) with the right pin_blocks per month. Required gating fixes for Q1: extended V3 events query (Dec 31 2025 → Mar 31 2026, captured the Feb 4 LP creation) and Chronicle NAV overrides for E7 STAC + E22 ACRDX at Dec 31 2025 (oracle pre-deployment).
- **RPC defensive coding.** `balance_of` / `scaled_balance_of` / `convert_to_assets` now treat empty (`0x`) returns and HTTP 4xx as zero — required for venues that didn't exist at older SoM blocks (E23 Steakhouse Prime Instant created mid-March; querying Feb SoM returns empty without this).

**Recently shipped (2026-05-02 — Grove team workbook reconciliation):**
- **Subsidised borrowing rate** — implemented per debt-rate-methodology Step 1.b. Formula: `subsidised_apy_d = ref_rate_d + (BR_d − ref_rate_d) × T / 24`, where T = months elapsed since 2026-01-01 (Sky governance program start), capped at 24. Applied to first `subsidy.cap_usd` of utilized USDS ($1B per prime); excess at full BR. Per-prime config in `{prime}.yaml::subsidy`. **Per Atlas A.2.8.2.2.2.2.2, every prime uses the 3M T-Bill** — Spark moved off EFFR on 2026-07-30 (QUESTIONS.md S5 resolved in Atlas's favour; the EFFR column was deleted). Daily rates in `config/subsidy_reference_rates.yaml`; `subsidy.ref_rate_kind` still selects the series but `tbill_3m` is the only accepted value.
- **SDE config table (`config/sky_direct_exposures.yaml`)** — replaces the per-venue `Venue.sky_direct: bool` flag with a time-bounded table supporting `kind: fixed|capped|pattern`. Active entries: Treasury Bills on Eth (BUIDL/JTRSY/USTB), PSM3 USDC non-Eth, Spark USDT/Curve. Historical: JAAA on Eth capped at $325M (2025-10-23 → 2026-03-12).
- **Capped SDE — daily-resolved sd_share (current, 2026-06-04).** For `kind: capped`, `sd_share = Σ_d cum_value_d / Σ_d uncapped_value_d` (the value-weighted average of the daily Sky-allocation fraction); `sd_revenue = actual_revenue × sd_share`. See `_capped_sd_revenue_daily_resolved` in `src/settle/compute/prime_agent_revenue.py`. Matches Grove team's PnL workbook per-day allocation methodology (the `<Asset>_ETH Allocation` sheets, col H × daily NAV) — verified against Jan/Feb 2026 JAAA_ETH within $3K–$6K. Coincides with EoM-locked snapshot when the position is stable across the period (e.g. Feb 2026 JAAA: $454M throughout); diverges materially only when the position moves mid-period (Jan 2026 JAAA: $751M → $454M; daily method gives 60.6% share vs EoM-locked 71.5%).
- **Burn-day override (capped SDE).** When the SDE entry has a `burn_date` inside the period AND `value_eom < cap_usd`, the daily-Σ method short-circuits to `sd_share = 1.0` (Sky absorbs the full period's `actual_revenue`). Rationale: Grove's workbook treats the burn-month's net P&L as essentially Sky's (JAAA Mar 2026: Sky takes −$451,060 of −$458,298 = 98.4%). The override is needed because daily-Σ under-attributes when `cum_value` drops to 0 from `usdc_settlement_date` onward — Grove's view is that the cap-protected slice spanned the bulk of the value-weighted exposure and the residual on-chain position is a small Grove-only sliver. The `value_eom < cap_usd` guard prevents firing when the position is still above cap at EoM (defensive). Verified vs Grove Mar 2026: matches `JAAA_ETH_Sky = -$451,060` to within $26K (residual gap is upstream `actual_revenue` drift from Centrifuge `Deposit/Withdraw.assets` events vs `Transfer × NAV`, not the sd_share decision).
- **Methodology evolution.** Pre-2026-06: per-day `sd_share_d = min(cap, v_d) / v_d` summed against daily revenue (was correct for stable months, fell back to SoM-locked summary). 2026-06-01 PR #101: switched to EoM-locked snapshot `min(cap, value_eom) / value_eom` applied to full `actual_revenue` — thought to match Grove based on Feb/Mar comparison (where the position was stable / fully out-of-cap and all three methods coincide). 2026-06-04: Jan investigation showed Grove uses daily-resolved; reverted to daily-resolved + added burn-day override to preserve Mar behaviour. Σ Jan–Apr 2026 JAAA sd_revenue: Grove $1,979,614 / daily+override $1,944,283 (gap −$35K = upstream actual_rev drift, sd_share matches on all four months).
- **Net Subs base refactor** — SDE asset values are subtracted from utilized in `compute_sky_revenue` (so Grove pays BR only on non-SDE allocations); SDE actual revenue flows directly to Sky on top. The legacy "shortfall floor" concept is retired (always 0 under the new model).
- **Centrifuge `pricePerShareFeed` NAV oracle** — new `INavOracleSource` kind backed by `convertToAssets(1e18)` on the per-tranche feed contracts (per `docs/pricing/allocation_pricing.csv` "Oracle2: centrifuge API" notes). E8 JAAA → `0x4880…0B`, E9 JTRSY → `0xFE69…77A`. Both reproduce Grove team's actual_revenue within $100/month (vs ~$535K/$146K diffs under the previous Chronicle path). Chronicle remains documented as a secondary feed but not auto-fallback.
- **Venue `skip: true` flag** — venues whose oracle/underlying is too volatile or too unreliable to include in MSC are skipped at compute time but kept in YAML for documentation. E21 (GACLO-1) remains skipped — no reliable NAV feed; Galaxy yield is to be recognized via monthly USDC sweep to ALM (Cat A via `external_alm_sources`). E20 (JAAA-avalanche) **un-skipped** in PR #67 (2026-05-11): Chronicle oracle at `0x02cf8c9fba24d79886dac40cb620f0930c6e8ec0` on Ethereum verified working Dec-2025 onward (NAV $1.020–$1.028); cross-chain block translation via `oracle_chain: ethereum` is handled automatically by the pipeline. **Empirical caveat (verified on Dune 2026-05-11):** the GACLO-1 issuer at `0x5ee36f573f0e543f905796c0e697caa7e984e0c8` has sent zero USDC to any address since the 2025-12-16 subscription event, and Grove ALM Avalanche has received zero USDC inbound ever — so the assumed Galaxy monthly USDC sweep mechanism is **not yet observable on-chain**. E21 currently contributes $0 to revenue (correct under `skip: true` + empty `external_alm_sources`). Q-G21 tracks confirmation of the payer / cadence / settlement asset with Grove.
- **E2 aHorRwaUSDC dust fix** — Aave V3's full-exit dust (1 raw unit remains after burn) blew up the closed-form `bal_eom × scaled_som / scaled_eom`, producing a phantom −$232K loss in Feb 2026. Threshold widened to detect post-burn dust under 0.1% of entry-time scaled balance.
- **Curve LP `sde_coin` field** — new optional field on `CurveIdleUsdsConfig` (`src/settle/domain/primes.py`) to handle Cat F venues where the SDE exposure is a *different* par-stable coin from the spread-revenue coin. Used by Spark **S24 sUSDS/USDT** Curve pool: `coin: sUSDS` drives the 30bps spread-revenue path (RULES §5); `sde_coin: USDT` drives the SDE asset-value path (`compute_sky_revenue` utilisation exclusion). The named SDE coin must be in `KNOWN_PAR_STABLES_ETHEREUM` (priced at $1/unit). Same mid-period-pro-rating limitation as the SDE table — see `config/sky_direct_exposures.yaml` KNOWN LIMITATION block + Q-S24.

**Deferred (PRD-flagged):**
- **Idle USDS/DAI in lending pools / AMMs (doc Step 2)** — doc lists Aave/SparkLend/Compound and Curve/Uniswap/Balancer USDS holdings as idle credit. No Grove venue currently holds USDS this way ($0 gap for Grove); scaffolding to be added when first prime needs it.
- **Reconciliation gap with Sky's reported Sky Share** (~$1.13M for Grove March 2026). The methodology + Sky Direct set are correct; the residual implies Sky uses a specific BR_charge "Asset Value" formula we haven't matched. Three approximations bracketed the answer (midpoint avg, daily time-weighted, EoM-only); next step would be either Sky's exact spec or per-tx pricing across the period.
- **Chronicle adapter robustness** — currently silently falls back to `const_one` ($1) when Chronicle returns `0x`, which can produce phantom revenue at SoM blocks before the oracle was deployed. Mitigated by `nav_overrides` fixture for known-historical NAVs at affected blocks, but the adapter itself should distinguish "pre-deployment" from "real $1" and refuse the const_one fallback for venues whose actual NAV is far from $1. **Partially mitigated 2026-05-13:** E22 ACRDX fallback replaced with `erc4626_vault` oracle (see §17.13 item 8).

**Diverges from Maker's official query (`6954386_daily_utilized_usds`):** the official Maker query does not include the PSM term. Our `utilized` formula is more generous to the prime when PSM holdings exist. For **Grove** the values match — Grove has no L2 PSM3 and no Ethereum PSM is tracked (mainnet stack is non-custodial). For **Spark** the L2 PSM3 holdings are material (~$544M USDS-equivalent as of 2026-05 across Base / Arbitrum / Optimism / Unichain — verified via `psm3_shares` + `convertToAssetValue` RPC calls). Of the three legs (see §17.11), only the **USDS leg** is subtracted from `utilized` directly; the **USDC leg** is routed into `sde_asset_value` (which is also excluded from BR base, but separately attributed to Sky as Sky Direct revenue); the **sUSDS leg** stays in the BR base — the prime pays full BR on it and is credited 30 bps as Prime Revenue to neutralise the SSR-via-share-price + BR-charge composite. Net divergence vs. Maker: Sky-side BR revenue is lower by ~`(USDS leg) × subsidised_BR + (USDC leg × subsidised_BR − USDC_SDE_yield)`; the sUSDS leg is BR-neutral on both sides.

### 17.4 Migration plan delta (vs. §13 above)

| Phase | Original status (§13) | Actual status |
|---|---|---|
| 1 — Plumbing + OBEX | "9 PRs to ship" | ✅ shipped |
| 2.A — Grove Ethereum | "Σ cost_basis ≈ cum_debt within 1%" | ✅ shipped (gap was multi-chain, not Ethereum) |
| 2.B — Grove Base + Avalanche + Plume | rolled into above | ✅ shipped — cost-basis +0.06% in March (within tolerance); +0.63% with E23 added |
| 2.C — Grove Monad | rolled into above | ⚠️ partial — venue identified but RPC archival outside available range |
| 2.D — Methodology alignment (Cat A polarity, sUSDS cost basis, PSM, Step 4 Sky Direct) | (added late) | ✅ shipped — all in §17.7. Q1 2026 Grove numbers reproducible |
| 2.E — PSM3 mechanics + per-prime PSM config (Spark prerequisite) | (added late) | ✅ shipped — `PsmKind.ERC4626_SHARES` + per-chain `Prime.psm` (L2 PSM3 only; mainnet PSM is non-custodial, not tracked). See §17.11. |
| 3.A — Spark scaffolding | planned | ⚠️ **partial** — `config/spark.yaml` (51 venues + 4 PSM3 stanzas across 6 chains; Eth has no PSM stanza), ilk + subproxy verified, start_date confirmed (2024-11-18). Q1 2026 numbers NOT yet computed — see §17.12 for remaining work. |
| 3.B — Skybase | planned | ❌ not started |
| 4 — Indexer alternative | planned | ❌ not started |

### 17.8 Q1 2026 Grove numbers — reproducibility note

`scripts/run_grove_2026.py` produces January, February, and March 2026 settlement numbers in one run (alongside Apr / May, which use the `grove_2026_04` and `grove_2026_05` fixture dirs). For the Q1 months, it uses the same lifetime Dune fixtures captured for March (debt, balances, SSR, mint/burn, cum_balance for RWA, V3 events, blocks_at_eod) and varies only `pin_blocks_som` / `pin_blocks_eom`. Two extensions vs the March-only run:

1. **V3 events fixture** widened from `[Feb 28, Mar 31]` to `[Dec 31 2025, Mar 31 2026]`, capturing the Feb 4 IncreaseLiquidity event ($25M USDC into the AUSD/USDC LP).
2. **`nav_overrides`** fixture key (in `dune_outputs.json`) provides explicit Chronicle NAVs for two `(oracle, block)` pairs where Chronicle had not yet started writing: E7 STAC at block 24136052 → $1000.00 (deposit-time par), E22 ACRDX at block 24136052 → $1.00 (deposit-time par). Without these, the prime_agent_revenue for January would include a phantom $100M jump on E7. **Note (2026-05-13):** the E22 ACRDX override is superseded by the `erc4626_vault` fallback oracle (see §17.13 item 8) — the vault gives the actual on-chain NAV ($1.01877 at block 24,136,052) rather than the $1.00 par approximation. The E7 STAC override remains necessary.

Tests: 211 unit + 8 integration passing, no regressions vs the post-Sky-Direct state.

### 17.9 Operational checks (recurring)

These are not blockers for the current pipeline but should be periodic operational audits. They protect against silent drift between the codebase, the Atlas spec, and on-chain reality.

#### Sky Direct exposure list — track Atlas changes
The set of exposures that qualify for Step 4 reimbursement is governed by Sky governance and recorded in the Sky Atlas (see [`sky-ecosystem/next-gen-atlas`](https://github.com/sky-ecosystem/next-gen-atlas)). Today the list is hardcoded in `config/sky_direct_exposures.yaml` (a time-bounded SDE config table; see PRD §3, "Step 4"). When a new exposure type is approved or an existing one is removed, this file must be updated.

> **Note (2026-05-04):** the older per-venue `sky_direct: true` flag in `<prime>.yaml` is **deprecated** — it's preserved for legacy YAML round-trip but ignored by compute. SDE classification is driven entirely by `config/sky_direct_exposures.yaml`. Adding `sky_direct: true` to a venue today is a silent no-op.

- **Recurring check (manual):** before each settlement cycle, diff the Atlas's Sky Direct exposures section against `config/sky_direct_exposures.yaml`. Note the Atlas commit/date when the current list was last reconciled, e.g.: "Sky Direct list as of Atlas commit `<sha>` (`<date>`): Treasury Bills on Ethereum (BUIDL, JTRSY, USTB); USDC in PSM3 on non-Ethereum chains; USDT in sUSDS/USDT Curve pools."
- **Future automation:** parse the Atlas repo (presumably structured Markdown/YAML) and surface diffs vs. our config in a CI check, or generate `config/sky_direct_exposures.yaml` directly from the Atlas as the source of truth.

#### On-chain flow-of-funds reconciliation — coverage check
The current venue list per prime is built by hand (PRD §3, §4) and cross-checked against external sources (e.g., BA Labs `stars-api.blockanalitica.com` — which surfaced the missing E23 Steakhouse Prime Instant on Base in this cycle). It's possible to miss a venue that the prime started using between settlements.

- **Recurring check:** trace USDS / underlying-asset flows out of the ALM proxy on each chain, follow them through swaps / deposits, and verify every destination contract is present in `config/<prime>.yaml` (or explicitly classified as a swap/PSM conduit, not a venue). The fixture `inflow_by_counterparty_e15` already captures the per-counterparty flow at one ALM (Grove Ethereum, USDC); the same Dune query parameterized by token + holder gives full coverage per (chain, ALM) pair.
- **Output:** a list of "addresses that received material funds but are not in the venue list", flagged for review.
- **Future automation:** add a `settle audit flow-of-funds --prime <id> --month <YYYY-MM>` subcommand that runs this diff and fails if any unrecognized counterparty crossed a configurable USD threshold.

#### Mainnet PSM non-custodial assumption — periodic sanity check
Today no Ethereum PSM is tracked in either prime's YAML. This rests on the empirical fact (verified 2026-05 on Dune + end-to-end tx trace) that Sky's mainnet PSM stack — `DssLitePsm` orchestrator (`0xf6e72db5…3042`), USDC pocket EOA (`0x37305b1c…7341`), `DaiUsds` converter (`0x3225737a…276a`), and retail `UsdsPsmWrapper` (`0xa188eec…f98c`) — is **non-custodial for any prime**: primes transit through it as atomic swaps within a single tx and accumulate no balances at any of those addresses. If Sky ever flips this assumption (a new custodial PSM that holds USDS, USDC, or sUSDS on a prime's behalf), the pipeline silently under-tracks the prime's idle capital until we add the new contract.

- **Recurring check (manual, every settlement cycle):** verify the assumption with two one-shot probes:
  - **USDS-at-PSM Dune query:** `SELECT SUM(amount) FROM tokens.transfers WHERE token = USDS AND "to" = UsdsPsmWrapper AND block_date >= period_start` plus the reverse; net should be ≈ 0 (dust). Same for direct interaction with the LITE-PSM pocket if a USDS leg ever appears.
  - **On-chain `balanceOf` snapshot at EoM:** `balanceOf(USDS, LITE_PSM | UsdsPsmWrapper | pocket)` and the equivalent for DAI / USDC. Both LITE-PSM internal DAI buffer (~$400M) and pocket USDC reserves (~$3.9B) should remain protocol-owned, not flagged as any specific prime's claim.
  - If either probe surfaces a per-prime accumulating balance, **re-introduce per-prime PSM tracking** as a new YAML kind (likely share-based, like PSM3 — not the old directed_flow).
- **Future automation:** add a `settle audit psm-custodial --month <YYYY-MM>` subcommand that runs both probes and fails if any per-prime balance exceeds a configurable threshold (~$10K dust by default).

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

### 17.11 PSM mechanics (custodial PSM3 only; mainnet has no per-prime tracking)

PSMs are declared per-prime in YAML under `addresses.<chain>.psm:`. Today only one mechanic is supported:

| Mechanic | When | How USDS-equivalent is computed |
|---|---|---|
| `erc4626_shares` | Spark PSM3 (Base / Arbitrum / Optimism / Unichain) | PSM3 is **custodial**: the prime's ALM holds shares against a basket of USDC + USDS + sUSDS reserves. Shares are *internal accounting* (no ERC-20 Transfer events), and the rate uses `convertToAssetValue(uint256)` (selector `0x41c094e0`) which returns the USDS-equivalent value of N shares directly. We snapshot `convertToAssetValue(shares(alm, b), b)` at each day's EoD block, then **decompose into the three legs** for per-leg routing below. |

**Why no Ethereum PSM entry.** Sky's mainnet PSM stack (`DssLitePsm` orchestrator at `0xf6e72db5…3042`, USDC pocket EOA at `0x37305b1c…7341`, `DaiUsds` converter at `0x3225737a…276a`, retail-facing `UsdsPsmWrapper` at `0xa188eec…f98c`) is **non-custodial for primes** — every prime↔PSM interaction completes atomically in a single swap tx. Verified end-to-end on Dune (Spark tx `0x2c81d2de…42c0b6`): the prime ALM transits USDS → DAI (via DaiUsds mint/burn) → USDC (transferred from the pocket via `DssLitePsm.buyGem`), with no balances accumulating at any of those addresses on the prime's behalf.

Notes on the stack's own mechanics (not relevant to prime accounting, but worth being precise so future readers don't repeat the conflation we hit during this audit):

* **`DaiUsds`** is pure mint/burn — `usdsToDai` burns USDS and mints DAI; `daiToUsds` does the reverse. Current contract balance: ~$0.006 dust both sides. No "reserves".
* **`DssLitePsm`** actually does hold a working DAI balance (~$402M empirically — verified 2026-05-11). It accumulates DAI deposited by `buyGem` callers until a `trim()` call burns the excess back to the vat. `fill()` is the inverse — it mints fresh DAI into the contract via `vat.suck` when the balance drifts below the `buf` target.
* **`fill()` and `trim()` are permissionless** — anyone can call them. Empirically 17+ distinct EOAs call them (top one ~588 fills + 559 trims). There is **no on-chain tip paid to the caller** (verified by checking the top caller's token receipts — zero DAI / USDS / USDC inflows). The keepers are Sky-funded community services and opportunistic MEV actors who pay gas as protocol maintenance, not contract-incentivised auction bidders.

Both the pocket's USDC reserves (~$3.9B) and `DssLitePsm`'s DAI buffer (~$402M) are **Sky's protocol working capital, not per-prime claims**. None of it should ever be reimbursed to a prime via `utilized` reduction.

An earlier `directed_flow` PsmKind tracked net USDS flow at the pocket — it was removed (2026-05-11) once we confirmed USDS doesn't touch the pocket at all (it only handles USDC); the tracker was looking at the wrong token at the wrong address. If a future Sky PSM ever becomes custodial for USDS, that's a new contract with a likely-different mechanism (almost certainly share-based like PSM3, not directed-flow), so re-introducing the pattern wouldn't be by reviving this enum value.

#### Per-leg split for `erc4626_shares` (PSM3) — implemented 2026-05-11

`convertToAssetValue(shares)` returns one number that bundles all three reserves. The methodology treats each leg differently, so we decompose Spark's claim per-day into:

- **USDS leg** → subtracted from `utilized` (BR-reimbursed). No SSR is paid on USDS, so simply zeroing the BR charge produces economic neutrality: net to prime = net to Sky = 0.
- **USDC leg** → **Sky Direct Exposure** per Atlas §A.2.3.2.2.3 *"USDC in PSM3 on non-Eth chains"*. The asset value is added to `cum_sde` in `compute_sky_revenue` (so the prime pays BR on that slice, not reimbursed); the actual yield (~$0 for passive USDC reserves) flows to Sky. This implements **Q-S23**.
- **sUSDS leg** → **not** subtracted from `utilized` (prime pays full BR on this slice), AND the orchestrator credits the prime `30 bps × value × n_days` as Prime Revenue via `_psm3_susds_spread`. Economic intent: the sUSDS share-price appreciation pays the prime `+SSR` automatically (the PSM3 holds sUSDS, so `convertToAssetValue` grows at SSR for the sUSDS slice); charging full BR `−(SSR + 30 bps)` and reimbursing `+30 bps` makes the composite `+SSR − (SSR + 30 bps) + 30 bps = 0`. Both prime and Sky net to zero on the sUSDS slice — neutrality on idle capital, matching the principle that "primes should neither pay interest nor earn money for idle USDS / sUSDS". Subtracting the sUSDS leg from `utilized` (without the spread credit) would instead leave Sky paying SSR with no offset — a subsidy.

Same shape as the existing rule for sUSDS held inside Curve LP pools (RULES §5 / `_aggregate_curve_idle_usds` → `curve_susds_spread`).

Decomposition is per-day: at each EoD block we read `balanceOf(token, psm3)` for each leg, translate the sUSDS face balance to USDS-equivalent via Ethereum sUSDS `convertToAssets(1e18)` at the matching EoD block (the L2 sUSDS is a 1:1 bridge of mainnet sUSDS — verified to 4 decimals across all 4 L2 chains), then apportion Spark's claim via `spark_share = convertToAssetValue(spark_shares) / pool_total_usds_eq`.

Token addresses for the per-leg reads live in `settle.domain.sky_tokens.PSM3_LEG_TOKENS` (discovered via Dune queries 7468346 + 7468351). `get_psm_usds_timeseries` returns a 6-column frame: `[block_date, daily_net, cum_balance, cum_usdc, cum_usds_leg, cum_susds]`.

Per-chain timeseries are summed by `_aggregate_psm_usds` into a single 6-column `psm_usds` DataFrame fed into `compute_sky_revenue`. For multi-chain primes (Spark), L2 PSM3 holdings reduce the Ethereum-denominated `utilized` since they were funded from Eth-borrowed USDS that was bridged over.

YAML snippet (Spark):
```yaml
addresses:
  ethereum:
    # No psm: block — mainnet PSM stack is non-custodial, no per-prime
    # balances to track.
  base:
    psm:
      kind: erc4626_shares
      address: '0x1601843c5e9bc251a3272907010afa41fa18347e'   # PSM3 Base
  # ... arbitrum, optimism, unichain similar
```

Grove numbers preserved exactly across this refactor (regression-checked). The 4 PSM3 venues in `config/spark.yaml` (S33/S40/S46/S50) were removed from the venue list — they're now PSM holdings, not Cat E venues.

### 17.12 Spark — current status (2026-04-29)

#### Done
- **Config scaffolding** (`config/spark.yaml`): 51 venues (Cat A: 15, Cat B: 17, Cat C: 12, Cat E: 5, Cat F: 2) across 6 chains (Ethereum, Base, Arbitrum, Optimism, Unichain, Avalanche-C). Per-chain PSM stanzas (4 — L2 `erc4626_shares` only; Ethereum has no PSM stanza since mainnet PSM is non-custodial, see §17.11).
- **Identifiers verified via Dune** (query 7399640): ilk = `ALLOCATOR-SPARK-A` = `0x414c4c…000000`, urn (borrower position in the Vat, most active allocator across all primes) = `0x691a6c29e9e96dd897718305427ad5d534db16ba`, first frob = 2024-11-18 (block 21,215,063), 49,794 frobs through 2026-04-29. **SubProxy** (idle-USDS holder earning agent_rate, distinct from the urn) = `0x3300f198988e4C9C63F75dF86De36421f06af8c4`, holds ~$30–37M USDS throughout 2026 funded via pre-period Sky governance allocations whose `Transfer` events Dune `tokens.transfers` doesn't surface; on-chain `balanceOf` is the canonical source (see §17.13 code-review acks 2026-06-09 entry). The earlier identification of the urn as the "subproxy" — corrected 2026-06-09 — was the root cause of `agent_rate = $0` in pre-2026-06 Spark runs.
- **Sky Direct flags**: 4 venues confirmed Sky Direct (S19 BUIDL-I, S20 JTRSY, S21 USTB on Ethereum + S24 sUSDSUSDT Curve). PSM3 holdings are NOT venues so the prior 4 Sky-Direct flags on the PSM3 venues went away with the refactor.
- **PSM3 ABI validated** (2026-04-29). On-chain probes of Base PSM3 (`0x1601843c…`) and Optimism PSM3 (`0xe0f9978b…`) confirm PSM3 does **not** implement standard ERC-4626 `convertToAssets(uint256)`. The contract exposes:
    * `shares(address) → uint256` — selector `0xce7c2ac2` (internal share balance; no ERC-20 Transfer events)
    * `convertToAssetValue(uint256 numShares) → uint256` — selector `0x41c094e0` (USDS-equivalent, 18-dec)
    * `convertToAssets(address asset, uint256 numShares)` — different signature than ERC-4626's; not used here
  
  Code now uses the correct ABI: new `IPsm3Source` protocol + `RPCPsm3Source` impl + `psm3_shares` / `psm3_convert_to_asset_value` in `extract.rpc`. The `erc4626_shares` branch of `get_psm_usds_timeseries` reads daily snapshots `convertToAssetValue(shares(alm, b), b)` for each day in the period.
- **Spark sky-revenue runner** (`scripts/run_spark_2026_q1.py`) built — now deprecated (`sys.exit(2)` guard) and superseded by `run_spark_2026_q1_full.py`. The deprecated script's PSM timeseries was Eth directed-flow + L2 PSM3 RPC; today only L2 PSM3 is tracked (mainnet PSM stack is non-custodial, see §17.11) and is built from the full per-leg snapshot via `compute_monthly_pnl` → `_aggregate_psm_usds`.

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
| E (RWA) | 5 | BUIDL-I + JTRSY share existing pricing paths with Grove. **USTB and USCC** (Superstate) need a new oracle (no Chronicle feed; Superstate publishes NAV via API). **Anchorage** is a $150M off-chain custodial position — interest sweeps now flow into `prime_agent_revenue` via Cat A (PR 1, 2026-05-05) using the `external_alm_sources` mechanism; Sky already charges BR on the funding via the standard ilk-debt mechanic, so the monthly-settlement bias is closed. Snapshot-module position value (`Snapshot.assets_usd` line item) and accrual-basis interest are deferred under issue #17. |
| F (LP) | 2 | Both Curve stableswap, same path as Grove E11. PYUSDUSDS adds PYUSD to the par-stable registry; sUSDSUSDT pricing needs sUSDS price (yield-bearing — check if Curve's `virtual_price` already accounts for it). |

#### Q1 2026 Spark numbers — sky_revenue ✅ SHIPPED, prime_agent_revenue IN PROGRESS

**Sky revenue (Q1 2026 — completed 2026-04-29):** $25.74M total via `scripts/run_spark_2026_q1.py`. Per-month: Jan $9.08M, Feb $8.36M, Mar $8.30M. Artifacts at `settlements/spark/{2026-01,02,03}/sky_revenue_only.json`. PSM3 reads via RPC (drpc) sampled at month-boundary dates with linear interpolation (full daily reads were prohibitively slow on drpc Arbitrum). Subsidised rate ramp NOT applied (Spark is ~17/24 mo into ramp; applying it would reduce sky_revenue per debt-rate-methodology file 2).

**`prime_agent_revenue` — venue inventory + status (2026-04-29):**

51 venues across 6 chains. Categorization + blocker status:

| Cat | # venues | Pricing path | Status |
|---|---|---|---|
| **A** (idle par-stables) | 15 | `_cat_a_capital_inflow_timeseries` — needs `cum_balance` per venue (Dune), source-tagged inflow if `external_alm_sources` non-empty | Spark's `external_alm_sources` lists the Anchorage Spark escrow (PR 1, 2026-05-05) → S26 USDC raw captures the monthly Anchorage interest sweeps as Cat A revenue (+$2.59M/Q1). Other Cat A venues (PYUSD, USDT, DAI, USDe, USDS raw) still revenue = $0 — no off-chain yield source registered. |
| **B** (ERC-4626) | 17 | `_shares_to_usd_inflow_timeseries` — `cum_balance` (shares) per venue (Dune) + `convertToAssets` (RPC) at SoM/EoM | Most work as standard 4626. **sUSDe** (S16) needs verification it's a true 4626 (Ethena cooldown design); **sparkPrimeUSDC1** (S18) Arkis API ideal but `totalAssets()` is the runtime fallback per `docs/pricing/allocation_pricing.csv`. |
| **C** (Aave/Spark spTokens) | 12 | `_atoken_index_weighted_inflow` — `balance_at` + `scaled_balance_at` (RPC, scaledBalanceOf path). **No Dune fixture needed.** | All venues use the same scaledBalanceOf ABI; Aave V3 + SparkLend share the contract. RPC calls via Eth/Base/Arb/Op/Avalanche-C providers (need drpc-resilient retry, already in place). |
| **E** (RWA) | 5 | `_rwa_inflow_timeseries` — `cum_balance` per venue (Dune, with `min_transfer_amount` filter for BUIDL-style yield mints) + NAV oracle | **S19 BUIDL-I** + **S20 JTRSY** share Grove's oracle paths (Chronicle for JTRSY, const_one for BUIDL). **S21 USTB / S22 USCC**: no holdings as of Q1 2026 → const_one is fine; real Superstate oracle deferred. **S23 Anchorage**: $150M off-chain BTC custody; the venue tracks USDC at the Anchorage escrow EOA (a near-instantaneous pass-through that nets to ≈ $0 over any settlement period), and realised interest is captured separately on **S26** via Cat A `external_alm_sources` (PR 1, 2026-05-05). |
| **F** (Curve LP) | 2 | `_curve_lp_index_weighted_inflow` — `balance_of(LP)` (RPC) + Curve `pool.balances/get_virtual_price` (RPC). **No Dune fixture needed.** | **S24 sUSDSUSDT** (Sky Direct, BR_charge applies) — needs sUSDS underlying price (= `convertToAssets(1)`). **S25 PYUSDUSDS** — needs PYUSD added to the par-stable registry. |

**Blocker breakdown:**

* **Unblocked (50/51 venues):** Cat A all 15, Cat B 16/17 (all except sUSDe), Cat C all 12, Cat E all 5 (S23 reshaped 2026-05-05 — see §17.12 "Anchorage"), Cat F both 2. Pricing paths exist; needs only Dune fixture capture + runner wiring.
* **Sub-percent risk (1/51 venues):** S16 sUSDe — assumed true 4626 (totalAssets-based) until verified. (S18 sparkPrimeUSDC1 also uses a 4626 fallback; Arkis API is preferred per docs but not load-bearing — covered under the unblocked set.)

**Anchorage S23 — special handling worth flagging.** PR 1 (2026-05-05) wired the interest-capture half: the Anchorage escrow is in `external_alm_sources`, monthly USDC sweeps land on **S26** USDC raw (ALM idle) and flow through Cat A par-stable accounting into `prime_agent_revenue` (+$2.59M for Q1 2026); `principal_return_overrides` protects principal-correction events from being mis-classified as yield. S23 itself tracks USDC at the Anchorage escrow EOA — a near-instantaneous pass-through that nets to ≈ $0 over any settlement period, so its own contribution is $0 (correct, no double-count with S26). Sky has been charging BR on the funding via the standard ilk-debt mechanic the whole time (`Vat.ilks(ALLOCATOR-SPARK-A).Art` covers every USDS Spark has drawn — and Spark's Art ranged $3.0B–$3.6B throughout Q1, well above the $150M Anchorage commitment), so monthly_pnl on this venue matches Spark's view. Open follow-ups under issue #17 (balance-sheet / methodology, not numbers): snapshot-module position value (today $0 because the escrow holds ~$0 USDC), accrual-vs-cash methodology, and an automated principal/interest split at loan termination — these would land via the eventual `TRI_PARTY_LOAN` pricing category.

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
  * S23 Anchorage: principal disbursed 2025-12-15 → 2025-12-19 in many small USDC transfers from SLL → escrow `0x49506C3Aa028693458d6eE816b2EC28522946872` (totalling ~$155M, net ~$150M after a $5M return on 2025-12-19). Q1 interest sweeps back to SLL: $891,780 on 2026-01-22, $891,780 on 2026-02-23, $805,479 on 2026-03-24 (≈ 7.13% APR on $150M, matches the "Anchorage BTC 6M 7%" loan name). Verified on-chain — see §17.12 "Anchorage" entry.
  * **Implication:** Cat E contributes ~$0 to Spark's Q1 2026 prime_agent_revenue **only if Anchorage interest is excluded**. With Anchorage included via the on-chain interest-sweep flow, Cat E adds ≈ $2.59M (Jan+Feb+Mar interest) to Q1 prime_agent_revenue.
* **Cat B Eth fixture in flight (Dune query 7402163, execution `01KQDKHVCMZ0VP7MRMMSMK9FBB`):** ~600+ rows captured; full pagination ~700–800 rows total. Sample snapshot at 2026-03-31: S14 syrupUSDC ~$86M, S15 syrupUSDT ~$89M, S18 sparkPrimeUSDC1 ~$10M, S32 sUSDS ~$247M (still growing into Q1), others <$1M.
* **Cat B L2s fixture in flight (Dune query 7402168, execution `01KQDKHVS8CP7VR9HZ928NW3PS`):** ~100 rows captured; one venue (S34 Spark Base USDC) shows steady growth from $3M in Jan 2025 to $429M in Jul 2025 — likely a major TVL contributor.
* **Eth + Avalanche-C daily blocks fixture in flight (Dune query 7402172, execution `01KQDKJ5E0R9FA6DV3YQJTJH39`):** ~100 rows captured; need full ~180 to cover the period for both chains.
* **Saved fixtures so far:** `tests/fixtures/spark_2026_q1/{debt_timeseries.json, l2_daily_eod_blocks.json}`. Cat B + Cat E fixtures NOT yet persisted to JSON (data captured in conversation, not on disk).
* **Runner not yet built.** A `scripts/run_spark_2026_q1.py` rewrite to call `compute_monthly_pnl` (vs. the current `compute_sky_revenue`-only path) is the next step after fixture capture finishes.

**Realistic remaining work:** ~30 more MCP pagination rounds to finish Cat B Eth + Cat B L2s + remaining block fixtures, then write a Spark fixture loader (mirroring `tests/fixtures/grove_fixture_loader.py`), then build the runner. Estimated 1–2 more focused sessions. ✅ All shipped — see `## Q1 2026 Spark prime_agent_total_revenue ✅ SHIPPED` below.

#### Q1 2026 Spark prime_agent_total_revenue ✅ SHIPPED (2026-04-30)

> **⚠ Pre-PR-1 snapshot.** The numbers below are from the 2026-04-30 run, before PR 1 (2026-05-05) wired Anchorage interest capture. Re-running adds +$891,780 (Jan), +$891,780 (Feb), +$805,479 (Mar) → **Q1 prime_agent_total = $19,099,849** (+$2,589,039 vs. the table below). `sky_revenue` and `monthly_pnl` shift by the same delta on the prime side. See §17.13 review-acks for details.

Full `compute_monthly_pnl` ran end-to-end for Jan/Feb/Mar 2026 via `scripts/run_spark_2026_q1_full.py` and the new Spark fixture loader (`tests/fixtures/spark_fixture_loader.py`):

| Month | prime_agent_total | sky_revenue | sky_direct_shortfall | **monthly_pnl** |
|---|---:|---:|---:|---:|
| Jan 2026 | $5,721,164 | $10,449,914 | $0 | **−$4,728,750** |
| Feb 2026 | $5,078,985 | $9,853,356 | $0 | **−$4,774,370** |
| Mar 2026 | $5,710,661 | $9,799,389 | $0 | **−$4,088,728** |
| **Q1 total** | **$16,510,810** | **$30,102,659** | **$0** | **−$13,591,849** |

Top venue contributors (Q1 average):
* **S28 PYUSD raw at ALM**: $0 revenue ✓ (Cat A revenue=0 per the methodology fix; balance growth treated as capital movement)
* **S32 sUSDS at ALM**: ~$560K/month (30 bps spread on ~$2.25B) — spread-only revenue, not full SSR
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
* **Anchorage S23 — $150M tri-party loan, FULLY ON-CHAIN VISIBLE (corrected 2026-05-05).** Earlier (2026-05-01) we wrongly concluded "no on-chain footprint" for this position. Re-verification on 2026-05-05 found the complete flow on-chain via Etherscan:
  * **Anchorage Spark escrow** (EOA): `0x49506C3Aa028693458d6eE816b2EC28522946872`. Receives all SLL-side disbursements and originates all interest-payment sweeps back to the SLL.
  * **Anchorage holding wallet** (EOA, downstream of escrow): `0x8149c53ea54de2a62c9e4caef29478f1af4c7bd3`. Received exactly **$150,000,000** in 4 transfers between 2025-12-18 and 2025-12-19 (the day the loan started). Both wallets are near-empty in raw `balanceOf` today because Anchorage swaps the USDC into off-chain BTC custody to run the "BTC 6M 7%" strategy — but the principal-sent-out trail is fully readable.
  * **Disbursement schedule (2025-12-15 → 2025-12-19):** ~99 USDC transfers from SLL → escrow (~$1.5M each on a ~45-min cadence, totalling ~$155M; one $5M return on 2025-12-19; one $133.5K top-up 2026-03-27). Net principal at Anchorage = ~$150M from 2025-12-19 onward.
  * **Interest payments back to SLL (Anchorage escrow → SLL):** $891,780 on 2026-01-22, $891,780 on 2026-02-23, $805,479 on 2026-03-24, $891,780 on 2026-05-04. Annualised these average ≈ 7.13% APR on $150M, matching the "Anchorage BTC 6M 7%" loan name from `dune.sparkdotfi.result_spark_anchorage_usdc`.
  * **Loan terms (from Spark's Dune view):** start 2025-12-19, end 2026-06-16, fixed APR ≈ 6.5–7.13% (depending on which row you read), principal $150M.

  **Methodology — what we actually need on-chain:** every input we need to settle this position monthly is on-chain:
  * **Realized interest** (the only thing prime-side accounting needs from the position itself): the escrow→SLL Transfer flow. Classified as Cat A par-stable yield via the `external_alm_sources` mechanism (PR 1, 2026-05-05).
  * **Sky's BR charge on the funding**: handled by the standard `compute_sky_revenue` mechanic. Spark drew the USDS to fund Anchorage from Sky (verified on-chain — `Vat.ilks(ALLOCATOR-SPARK-A).Art` jumped +$208M between 2025-12-14 → 2025-12-19, comfortably covering the $155M disbursement). Spark's Art has stayed ≥ $3.0B throughout Q1 2026, well above the $150M Anchorage commitment, and Anchorage is neither PSM-netted nor SDE-reimbursed in `utilized`, so BR has been charging on it cleanly the whole time. **No new code needed for the Sky side.**
  * **Principal-correction events** (e.g., the $5M return on 2025-12-19): registered in `principal_return_overrides` so the Cat A classifier doesn't mis-classify them as yield (PR 1).

  **PR 1 closes the monthly-settlement bias on Anchorage.** Q1 2026 Spark `prime_agent_revenue` gains $891K + $891K + $805K = +$2.59M (the three Q1 interest sweeps); `sky_revenue` is unchanged because BR was already correct; `monthly_pnl` on this venue now matches Spark's view.

  **Open follow-ups under issue #17** — balance-sheet / methodology refinements, NOT correctness gaps for monthly settlement. These are the eventual scope of a `TRI_PARTY_LOAN` pricing category if/when we want it:
  - snapshot-module position value: today S23 reports `$0` in `Snapshot.assets_usd` because the escrow EOA holds ~$0 USDC at any given block (the principal lives off-chain in BTC custody). A `TRI_PARTY_LOAN` path would return `principal_at_block` (cumulative SLL→escrow flow net of returns) so the $150M shows up in the balance sheet during the loan term.
  - accrual-vs-cash methodology agreement with Spark (currently cash-basis — interest recognised on the day the sweep arrives; Spark's PnL workbook may accrue continuously).
  - automated principal/interest split at loan termination (currently the operator adds an entry to `principal_return_overrides` when the unwind transfer lands).

**Reconciliation against Spark's `dune.sparkdotfi.result_spark_*` materialised views (2026-05-01):**

Compared every per-venue value_eom for Q1 2026 against the equivalent row in Spark's materialised tables. **Most venues reconcile within $1K (sub-0.001%)**. Specific findings:

* ✅ **Cat A par-stables** (S28/S38/S39/S44/S45/S48/S49/S52/S53/S55): exact match on RPC `balanceOf` ↔ Spark's `alm_supply_amount`.
* ✅ **Cat C SparkLend** (S1–S5): match within ±$1K (sub-0.001%) at all 3 EoM dates. Tiny diffs are block-timing dust.
* ✅ **Cat C Aave** (S6/S7/S8 zero, S9 = $495,610,844 vs Spark $495,610,814 in March, S54 Avalanche $10M): match within ±$30.
* ✅ **Cat B Morpho/Maple/sUSDS variants** (S10/S12/S13/S14/S15/S32/S34/S37/S43/S47/S51): match within ±$5K (~0.001%). S32 Eth ALM Jan: ours $393.52M vs Spark `362.7M raw × 1.08497 conversion = $393.52M`.
* ✅ **PSM3 holdings** (utilized reduction): per-leg breakdown in Spark's view (USDC/USDS/sUSDS) sums to our aggregated USDS-equivalent within ±0.01%.
* ✅ **Cat F Curve LPs** (S24/S25): per-leg amounts in Spark's `result_spark_curve_pool_apr` reconcile to our LP-share value via reserves × per-coin price.
* ⚠ **Cat B Arkis sparkPrimeUSDC1 (S18) — persistent ~0.7% drift** at all 3 EoM dates (Jan +$105K / +0.70%, Feb +$15K / +0.14%, Mar +$58K / +0.57%). Spark's reported values are suspiciously round ($15.00M / $10.10M / $10.10M), suggesting their view consumes Arkis's API NAV directly while we use the on-chain `convertToAssets()` fallback. **Direction:** ask Spark/Arkis whether the on-chain `convertToAssets` is authoritative or if the API publishes a different NAV; the drift biases our prime_agent_revenue +$60–100K/quarter. **See QUESTIONS.md S14.**
* 🆕 **Foundation USDS — accounting construct, ~$0 P&L impact.** Spark's table publishes a "Foundation" row at $1.1M (Apr 2026 onward; was $400K earlier) with `gross_yield_apr ≈ borrow_cost_apr` by construction → net P&L is zero by design (Spark gets reimbursed at base rate for this position). It's NOT a logical sum of unutilized USDS across PSM/PSM3/Curve (size is too small to be that aggregate); rather a small designated operational treasury entry where the rates cancel. Safe to ignore in our compute. **No action needed.**

**Methodology observations from the Spark Dune comparison (no action needed but documented):**

* Spark publishes **a daily sUSDS conversion-rate view** (`result_daily_token_conversion_rates`). We compute it on-the-fly from `convertToAssets(1 share, block)` — same number within ~0.001% (block-time vs midnight-UTC snapshot drift).
* Spark splits **PSM3 holdings into 3 legs** (USDC/USDS/sUSDS) per chain. We aggregate to a single USDS-equivalent. The per-leg breakdown matters if Sky Direct treatment differs (USDC in PSM3 on non-Eth IS Sky Direct per methodology) — currently this isn't load-bearing because all our PSM3 USDS-equivalents flow into utilized reduction without per-leg differentiation.
* Spark's **per-table column conventions vary** (`alm_supply_amount` for lending, `amount` for Maple/Arkis, `spark_holdings` for Ethena where the table publishes Spark+Grove combined, `sll_allocated_assets_balance` for Curve). Reading the right column per table matters; we cross-checked each.
* Spark's **deployment-efficiency formula is `allocated / (liabilities − idle)`** (subtracts idle from denominator). Our utilized formula subtracts idle from numerator (`utilized = debt − alm_usds − psm_usds`). Note: subproxy balances are NOT subtracted — they are treasury/risk capital that earns agent rate, not reimbursable as ilk debt proceeds.
* Spark's view tracks **Aave Ethereum aUSDT first appearing 2026-03-31 ($495M)** — matches our S9 EoM exactly. No data pre-March (the position opened that month).

**Decision (2026-05-01):** treat Spark's Dune tables as a **reconciliation reference only**, not as a data source. Our pipeline reads on-chain + raw Dune `tokens.transfers` independently; we cross-check against Spark's tables when investigating discrepancies. This keeps us methodologically sovereign while leveraging Spark's view for sanity checks.

#### Spark Dune dashboard re-review (2026-05-04) — improvement areas surfaced

Spark's [SLL Assets to Liabilities dashboard](https://dune.com/sparkdotfi/spark-sll-nav-to-liabilities) is now public (daily 05:00-05:30 UTC update cadence). The underlying queries are private — we can read the dashboard's public visualization metadata (column shapes) but not their SQL. From the column shapes we extracted:

**Top-line (SLL — main USD-denominated balance sheet)** — 6 counters at row 3 + row 7. We can't see field names directly (queries private) but the layout maps cleanly to: total assets, total liabilities, surplus, allocated, idle, deployment efficiency.

**Per-protocol breakdown** (query 5776184, public visualization metadata):
```
blockchain | protocol | token | original_balance | susds_conversion_rate
| usds_equivalent_balance | SLL_allocated_assets_balance
| total_assets_balance | total_assets_usds_equivalent_balance
| idle_balance | idle_usds_equivalent_balance
```
Spark's table **splits each protocol holding into `allocated` vs `idle`** — within a single venue, some balance is in-use, some pending allocation. Our pipeline collapses this to a single `value_usd` per venue.

**spETH section (NEW)** — entirely separate ETH-denominated balance sheet (query 6866703, full column shape visible):
```
total_assets_eth | allocated_assets_eth | idle_assets_eth
| liabilities_sky_eth | liabilities_savings_v2_eth | total_liabilities_eth
| net_surplus_eth | deployment_efficiency_ratio
| eth_price | net_surplus_eth_usd | surplus_eth | surplus_eth_usd
```

**Three improvement areas surfaced — none of which we could see when we last reconciled (2026-05-01):**

1. **Spark Savings V2 (`pricing_category: S2`) has no compute path.** S56–S60 in `config/spark.yaml` are catalogued (spUSDC, spUSDT, spETH, spPYUSD on Eth + spUSDC on Avalanche) but the compute layer skips them with a warning per `domain/pricing.py:20-27`. That's ~**$2.3B+ of vault TVL** (per docstring: spUSDC ~$1B + spUSDT ~$1.13B + spETH ~$185M + spPYUSD ~$1M + spUSDC-avax ~$37M) currently invisible to our snapshot. Spark's dashboard explicitly tracks each as a separate balance sheet (vault's `totalAssets` deployed, `totalSupply × pps` owed to depositors, surplus = the spread). Resolution path documented as Q for Spark in QUESTIONS.md (Spark section).

2. **`liabilities` should = debt + savings_v2_liabilities.** Spark's spETH columns confirm a two-source liability model (`liabilities_sky` + `liabilities_savings_v2`). We already noted this pattern from BA labs (`liabilities = debt + sUSDS_POL` for Spark) — the dashboard now confirms it's a **published Spark methodology, not a BA quirk**. Our snapshot currently reports `liabilities = debt` only; should extend.

3. **Per-asset-class native-unit balance sheet.** Spark publishes spETH in ETH-units (with USD conversion at the headline only via `eth_price`). Other vaults in USD. Our snapshot uses USD-only across the board. For spETH this introduces price-translation noise that Spark doesn't have. To match: per-asset-class accounting + WETH oracle for the headline conversion. Lower priority since spETH is small ($185M) and the existing `nav_oracle_resolver` could carry an ETH-priced variant.

**Minor observations (no action needed)**:
- Spark exposes `susds_conversion_rate` as a first-class column (we compute on-the-fly via `convertToAssets(1, block)` — same value, different presentation).
- Spark's "idle vs allocated within a single venue" split is a nice-to-have but doesn't change headline numbers; we'd add it if BA / Sky governance asked.
- Daily-update cadence (05:00-05:30 UTC) confirms snapshot semantics — our `python -m settle snapshot --prime spark` produces the equivalent at any block.

**Decision (2026-05-04):** open three follow-up questions to Spark in QUESTIONS.md (Spark section) before any implementation. The Savings V2 surplus formula in particular hinges on what "Spark's seed principal" is per vault — need confirmation before the compute path lands.

#### Spark `result_*` table catalog (2026-05-04 update)

Spark publishes 44+ materialized views under the `dune.sparkdotfi.result_*`
namespace — these are public and queryable, even when their backing SQL is
private. Cataloged via Dune's `searchTables`; the table below lists the
**settlement-relevant subset** (~36 named tables, including a handful of
suffixed series like `result_target_depositor_daily_*` and `result_benmo_*`).
The remainder are dashboard-internal helpers (renderer outputs, KPI rollups)
not load-bearing for our reverse-engineering. Per the operating principle
(PRD §17.12 "treat as reconciliation reference, not data source"), we
**don't consume these at runtime**, but their public column shapes resolve
several open questions left over from the dashboard
re-review.

**Per-protocol "idle vs allocated" (answers Q S8):** every
`result_spark_*_by_alm_proxy` table exposes the same shape:
```
dt, blockchain, protocol_name, token_symbol, supply_index, borrow_index,
supply_rate_apr, borrow_rate_apr,
alm_supply_amount, supply_amount, borrow_amount, utilization,
idle_amount,                                   ← protocol-level idle (un-borrowed)
alm_share,                                     ← Spark's fraction of supply
alm_idle (= alm_share × idle_amount),          ← Spark's slice of the idle
borrow_cost_code, borrow_cost_apr,             ← Sky borrow rate Spark pays
[interest_amount, sparklend_revenue, ...]
```
The `alm_idle` column makes the idle/allocated distinction
**economically meaningful** — Spark's lending-pool supply that hasn't
been borrowed by counterparties earns the supply rate but is at risk of
de-allocation. Our `Venue` should expose `alm_supply_amount` and
`alm_idle` separately rather than a single value.

**Savings V2 vault surplus (answers Q S6):**
`result_savings_v_2_deployment_metrics` (`dt, token_symbol, total_amount,
holding_amount, deployed_amount, apr, borrow_cost`) gives the canonical
formula for spX vault accounting:
- `total_amount = holding_amount + deployed_amount`
- daily Spark surplus = `deployed_amount × (apr − borrow_cost) / 365`
- vault liabilities to depositors accrue at `borrow_cost`

Companion table `result_spark_savings_v_2_vaults_holdings` exposes
per-user balances by `(blockchain, vault_symbol, user_addr, dt)` for
referral attribution.

**BA `idle_assets` source (likely answer to Q B1):** sum
`alm_idle` across all `result_spark_*_by_alm_proxy` and
`result_spark_curve_pool_apr` tables likely reconstructs BA's
$720M `idle_assets` figure exactly. Pending confirmation, this means
we can compute it ourselves by reading per-protocol utilization + ALM
share via on-chain RPC for each lending pool.

**Anchorage off-chain feed (refines Q S3):**
`result_spark_anchorage_usdc` exposes `loan_name, loan_start_date,
loan_end_date, supply_rate_apr, alm_supply_amount, sky_borrow_amount,
gross_yield_formula, borrow_cost_formula, loan_status`. Either we
consume this table directly or ask Spark for the loan-terms YAML they
manually populate.

**Ethena S16 has Spark+Grove-shared accounting (NEW Q S9b):**
`result_spark_ethena_payout_apy` shows `total_holdings, grove_holdings,
spark_holdings, spark_share` — Spark and Grove share the Ethena
position. Our pipeline treats S16 as a flat Cat B venue without share
apportionment. Needs investigation.

**Liabilities = sky_debt + savings_v2_borrow (answers Q S7):**
`result_spark_sll_revenue_projection_raw_1` separates
`sky_borrow_cost_proj_usd` and `saving_v2_borrow_cost_proj_usd` as
distinct projection lines. Combined with the `borrow_cost_apr` column
present in every per-protocol table, this confirms a unified liability
model: `total_liabilities = sky_debt × subsidised_BR + Σ_v vault_borrow_cost`.

**Full table inventory (44 result_* tables in `dune.sparkdotfi`):**

| table | purpose |
|---|---|
| `result_spark_idle_dai_usds_in_sparklend_by_alm_proxy` | Spark's idle in SparkLend |
| `result_spark_idle_dai_usdc_in_morpho_by_alm_proxy` | Spark's idle in Morpho |
| `result_spark_idle_usds_in_aave_by_alm_proxy` | Spark's idle in Aave |
| `result_spark_aave_usdc_by_alm_proxy` | Aave USDC position |
| `result_spark_aave_ethereum_a_usdt_by_alm_proxy` | Aave aUSDT (S9) |
| `result_spark_aave_avalanche_a_usdc_by_alm_proxy` | Aave Avalanche aUSDC (S54) |
| `result_spark_arkis_spark_prime_usdc_1_by_alm_proxy` | Arkis Prime (S18) |
| `result_spark_maple_syrup_usdc_by_alm_proxy` | Maple syrupUSDC (S14) |
| `result_spark_maple_syrup_usdt_by_alm_proxy` | Maple syrupUSDT (S15) |
| `result_spark_anchorage_usdc` | Anchorage off-chain loan (S23) |
| `result_spark_morpho_markets` | Morpho market detail |
| `result_spark_pendle_farms` | Pendle positions |
| `result_spark_curve_pool_apr` | Curve LPs (S24/S25) |
| `result_spark_ethena_payout_apy` | Ethena S16 (Spark+Grove shared) |
| `result_spark_superstate_ussc` | Superstate USCC (S22) |
| `result_spark_savings_v_2_vaults_holdings` | Per-user spX holdings (S56–S60) |
| `result_spark_savings_v_2_vaults_time_weighted_average_holdings` | Per-user TW spX |
| `result_savings_v_2_deployment_metrics` | spX vault surplus formula |
| `result_spark_sp_usdc_sp_usdt_sp_eth_daily_balance` | spX daily TVL |
| `result_spark_sp_usdc_sp_usdt_sp_eth_time_weighted_average_balance` | spX TW per ref_code |
| `result_spark_spark_savings_balance_raw` | sUSDS/sUSDC raw event log |
| `result_spark_s_usds_s_usdc_time_weighted_average_balance` | sUSDS/sUSDC TW per ref_code |
| `result_spark_staked_usds_balances_by_referrals` | stUSDS by referral |
| `result_daily_token_conversion_rates` | Daily sUSDS↔USDS rate |
| `result_usds_total_deposits` | USDS total deposits (Sky-wide) |
| `result_aave_usdc_usdt_total_deposits` | Aave USDC/USDT totals |
| `result_us_de_total_deposits` | USDe totals |
| `result_spark_usds_s_usds_usdc_in_psm_3_curve_psm_3_proxy_foundation_aave` | **Raw USDS / sUSDS / USDC balances at non-protocol addresses** — covers PSM3 (per-chain, Base/Arb/Op/Uni), the Curve PSM3 proxy, the SLL ALM proxy itself (per chain), Foundation (Eth), and a residual Aave bucket. Schema: `(dt, blockchain, protocol_name, token_symbol, amount, gross_yield_apr, borrow_cost_apr)`. The slice `result_*_by_alm_proxy` does **not** capture (lending-pool side vs. raw-balance side). Latest-dt totals (2026-05-04 probe): ALM Proxy ≈ $4.32B (incl. ETH ALM sUSDS $2.25B = our S32 sUSDS POL, ETH USDT $746M = S27, PYUSD $678M = S28, plus L2 USDS/sUSDS POL ~$700M), PSM3 ≈ $520M, Foundation $1.1M. **This is the source for question B1's reverse-engineering of BA `idle_assets`.** |
| `result_spark_lend_time_weighted_average_borrow_amount_raw` | Borrowed amounts TW |
| `result_spark_sll_actual_revenue_daily` | Daily SLL revenue (different dashboard) |
| `result_spark_sll_actual_revenue_daily_raw_data_1` | Same, raw |
| `result_spark_sll_revenue_projection_raw_1` | Revenue projection (confirms liabilities split) |
| `result_spark_sll_revenue_projection_raw_2` | Same, raw |
| `result_target_depositor_daily_*` (4 tables) | Targeted depositor lists (referral) |
| `result_avalanche_sp_usdc_latest_depositor_list` | Avalanche spUSDC depositors |
| `result_s_2_points_claimed_spk_destinations` | SPK reward destinations |
| `result_benmo_*` (2 tables) | Misc — referral aggregations |

**Decision (unchanged):** still treat the `result_*` tables as
**reconciliation reference + reverse-engineering aid**, not as a
runtime data source. The catalog above closes Q S6, Q S7, Q S8 (move to
"internal todo"), reframes Q S3, and likely answers Q B1 (pending BA
confirmation).

Artifacts: `settlements/spark/{2026-01,02,03}/{pnl.md,pnl.csv,venues.csv,provenance.json}`.

### 17.12.1 Cross-prime methodology insights (2026-05-04)

Audit pass after the Spark `result_*` table catalog work: every methodology
improvement made for one prime should propagate to the others when the
underlying fact-pattern is shared. Conclusions per fact-pattern:

| Pattern | Source | Applies to | Status |
|---|---|---|---|
| Subsidised borrowing rate | debt-rate-methodology | Grove + Spark + future primes | ✅ cross-prime, single series (`tbill_3m` for every prime since 2026-07-30) |
| SDE config table | Sky governance | All primes | ✅ already cross-prime (`config/sky_direct_exposures.yaml`) |
| Centrifuge `pricePerShareFeed` for JTRSY/JAAA/ACRDX | Grove team workbook | Any prime holding Centrifuge tranches | ✅ Grove E9 (2026-05-02) → **Spark S20 aligned 2026-05-04** |
| `skip: true` for untrusted-oracle venues | Sky/Grove decision | All primes | ✅ already cross-prime |
| Aave V3 post-burn dust handling (E2 fix) | Grove | All primes with Aave V3 / SparkLend | ✅ shared via `_atoken_index_weighted_inflow` |
| Per-protocol `alm_idle = alm_share × protocol_idle` | Spark dashboard | Grove E1/E2/E3 + Spark S1–S15 + others | ⚠️ **field not yet on `Snapshot.types.VenueSnapshot`**. Grove E1 has 46% idle ($135M of $251M); Spark idle ≈ $465M visible across 13 venues. Visibility-only (no impact on monthly settlement); add as a new `VenueSnapshot.alm_idle` field when convenient. |
| `liabilities = debt + sUSDS_POL_value` | Spark dashboard / BA labs | Both primes when sUSDS POL > 0 | ⚠️ Grove E18 sUSDS POL = $0 today, so no current numerical impact. Snapshot's `liabilities_usd` should include `sUSDS_POL × $1` when non-zero. Cross-prime fix deferred until Grove holds sUSDS at the ALM. |
| Spark+Grove shared Ethena position via `spark_share` | `result_spark_ethena_payout_apy` | Both primes | Grove ALM holds **$0 USDe / $0 sUSDe** (verified on-chain 2026-05-04). Currently no Grove-side handling needed; if Grove adds Ethena exposure later, the apportionment logic should be ported. |
| Subproxy USDC reading | Grove (~$0.75M idle) | Both primes | ✅ Snapshot reads USDC at all subproxies cross-prime since 2026-05-02. |
| Raw token balances at PSM3 / ALM Proxy / Foundation (`result_spark_..._psm_3_proxy_foundation_aave`) | Spark dashboard | Both primes | ✅ Snapshot already reads ALM-side raw USDS/USDC/sUSDS via `_read_idle_holdings` + venue inventory (Spark S27/S28/S32 family + L2 USDS POL; Grove subproxy + ALM equivalents). Spark's published table provides a **public reconciliation point** for what we read on-chain. **Per-chain PSM3 holdings** are already covered by our `IPsm3Source` RPC reads (`extract/rpc.py::psm3_shares` + `psm3_convert_to_asset_value`) routed through `_aggregate_psm_usds`, but they're modeled as USDS-equivalent for sky_revenue netting, not as a venue. |

**Concrete change (2026-05-04):** Spark S20 JTRSY oracle switched from
Chronicle (`0x59ef…3d0d`) to Centrifuge `pricePerShareFeed`
(`0xFE69…77A`), mirroring Grove E9. Spark holds $0 JTRSY today (per BA
`/allocations`) so zero numerical impact, but if any volume lands the
methodology now agrees with Grove's workbook ($32 drift vs Grove team's
canonical pricing).

**Deferred but documented:**
- `alm_idle` snapshot field (Spark dashboard's per-protocol idle/allocated
  split) — economically meaningful, cross-prime applicable, but not
  required for monthly settlement. Worth adding when we revisit
  `Snapshot.types.VenueSnapshot` for any reason.
- `liabilities = debt + Σ vault_borrow_cost` (BA labs / Spark
  unification) — applies the moment Grove holds sUSDS POL or Spark spX
  vaults grow. Today Grove E18 = $0 and Spark spX skip-flagged, so
  current numbers unchanged. Add to `Snapshot.liabilities_usd` once
  there's a non-zero case.

### 17.13 Open questions (priority-ordered)

#### Methodology — resolved 2026-09: GAR retired from the MSC (operator decision)

**Governance Accessibility Rewards is no longer part of the MSC
calculation from 2026-08.** It was a Skybase-only Demand-Side primitive
equal to 1% of the same month's consolidated Sky Net Revenue, carried on
reports 2026-01…2026-07 (introduced per operator decisions 2026-08-06/07).
Skybase is the only prime affected — no other config ever declared a
`gar:` block.

Expressed as a BOUND, not a deletion: `GarConfig.until_month = '2026-08'`
(the first month WITHOUT GAR), set in `config/skybase.yaml`. Deleting the
primitive would mean a re-run of any settled month silently dropped the
line, and `settlements/**/provenance.json` is gitignored — so the stored
value would be unrecoverable. That regression has happened before:
January's demand side went 314,251.68 → 222,064.54.

**Effect on Sky Net Revenue.** `gar → dv → send`, and
`msc_net = total_mint − total_send − dsb`, so GAR *subtracts* from SNR.
Retiring it therefore RAISES SNR from 2026-08 by that month's GAR
(≈ $105K at July's level, ≈ 1% of SNR). The settled months are unaffected,
by two different mechanisms:

* **2026-01…06 — PAID basis.** `compute/sky_total.py` contains no
  reference to `gar` at all; SNR is anchored on the executed settlement
  transaction, so the primitive is structurally invisible to it.
* **2026-07 — ACCRUAL basis, pinned.** `msc_preview.2026-07.skybase`
  pins `send: 374489` and `gar_in_dv: 152255.89`, and the pinned figures
  win over the derived ones. Verified: July's SNR re-runs at exactly
  10,517,425.807934152 — including from a *gar-less* skybase provenance,
  which is the strongest form of the check.

Verified end-to-end by regenerating skybase 2026-01…08: Jan…Jul still
carry the row (January demand side back at 314,251.68, July's GAR
identical at 105,174.26) and 2026-08 carries none (demand side
101,204.19 = agent rate + DR). Locked down by
`tests/unit/test_gar_retired.py`.


#### Methodology — resolved 2026-09-01: BR/SSR rate units (operator-confirmed)

**The Base Rate is NOMINAL (APR); the SSR is an APY and is converted before
the spread is added.** Raised while auditing whether the per-second SSR is
honoured at daily granularity, and sharpened by Cloaky's observation that
the Atlas defines the demand-side rate as an APR. Confirmed by the MSC
operator 2026-09-01. Applied **going forward only** — no restatement; July
2026 settled at MSC#11 and its `sky_total` stays frozen at 10,517,425.81
via the `msc_preview` pins.

```
SSR_apr  = 12 x [(1 + SSR_apy)^(1/12) - 1]    = 3.464456%   at SSR 3.52%
BR_apr   = SSR_apr + spread                    = 3.664456%   (+20bps)
charge_d = utilized_d x BR_apr / 365                         (nominal)
```

**Why the units differ.** SSR compounds per-second into the sUSDS index
on-chain, so it is an effective rate. The spread and the subsidy reference
rate are governance/money-market numbers, i.e. nominal. Adding an APY to an
APR produces a rate of no defined type — which is what the code did, first
multiplicatively and then (2026-08-24) additively.

**Why n = 12.** The conversion is exactly invertible only if the accrual
compounds at the same frequency the conversion assumed. The charge
compounds when the MSC capitalises it into the ilk debt — monthly — so
n = 12 makes the conversion round-trip: `(1 + SSR_apr/12)^12 − 1` returns
the SSR APY (3.52%) to the digit. NB this holds for the CONVERTED leg, not
for `base_apr` — compounding that monthly gives 3.7266%, the APY equivalent
of the Base Rate. Converting at n → ∞ (`ln(1+APY)` = 3.459464%) and then
compounding monthly leaves a 0.52 bps/yr residual in the prime's favour;
n = 12 zeroes it.

**Cross-month compounding needs no code.** Allocator ilks carry `duty = 0`
and a frozen `vat.rate`, but Sky calls `vat.grab` with positive `dart` at
each settlement to fold the accrued charge into `urns[ilk].art`, and
`cum_debt` sums frob + grab. The enlarged principal pays BR from the
settlement day onward. A cross-month carry of accrued-but-unpaid interest
was considered and **rejected** — it would bill the same interest twice.

**Scope.** BR charge (and its gross series), agent rate, all 20 bps
reimbursement legs (Cat B sUSDS, PSM3, Curve) and Chronicle Points are
nominal and accrue simply. The **SSR-appreciation** legs (PSM3 appreciation,
Curve Case-3b, Savings-V2 depositor SSR) keep the APY daily factor and are
UNCHANGED from before this PR — simple sums. Their principal is already
mark-to-market (`convertToAssets` re-read daily), so it carries the
compounding; accumulating it again over-credits by ~0.14%. Nothing in the
repo compounds inside a settlement period, and `CompoundingAccrual` was
deleted.

**Why n = 12, and what it buys.** The conversion is exactly invertible only
if the accrual compounds at the frequency the conversion assumed. Two things
compound at exactly that frequency, and n = 12 reconciles both:

| | compounds | reaches over a year |
|---|---|---|
| prime's debt | monthly, as the MSC capitalises the net charge | `(1 + SSR_apr/12)^12 − 1` = **3.5200%** |
| prime's sUSDS | continuously, via the index | `(1 + SSR)^1 − 1` = **3.5200%** |

So the idle-sUSDS legs net to zero in **settled dollars**, not merely at the
rate level — simulated over 12 months on $1B financed 1:1, the Rule 5
composite is **+0.034 bps/yr** (day-count noise). Converting at `n → ∞`
(`ln(1+APY)` = 3.459464%) would leave the debt reaching only 3.5148% and
break the netting by **+0.549 bps/yr**, as well as under-charging BR on all
utilized debt by ~0.5 bps.

**A caution for anyone re-deriving this.** Comparing the two DAILY slices in
isolation — `SSR_apr/365` = 9.4917e-5 against the index's
`(1+SSR)^(1/365)−1` = 9.4784e-5 — shows a 0.14% gap and suggests a
−0.48 bps/yr residual. That comparison is wrong: it holds both principals
static, when in fact the credit accrues on a balance growing continuously
and the charge on one that is static within the month and steps up at
settlement. The two effects cancel by construction of n = 12.

**Reference rates.** Re-typed as APRs and used as published: the NY Fed
publishes SOFR as an annualised simple rate and the Atlas defines it as "the
rate (expressed as an annual rate) ... as administered and published by the
Federal Reserve Bank of New York", with a most-recent-prior carry-forward
that `ReferenceRateHistory.at()` already implements. Config field renamed
`sofr_apy` → `sofr_apr`; August 2026 back-filled from the API. The 3M T-Bill
column is deliberately NOT re-typed — it stopped being the reference on
2026-07-23 and its months are settled.

**Measured effect (July 2026 rates, per $1B):** unsubsidised 31-day charge
3,106,921 → 3,112,278 (+5,357/month, +0.13 bps); subsidised tranche
3,064,438 → 3,101,776 (+37,338/month, ≈+4.3 bps) — nearly all of the latter
from the reference rate being re-read as an APR without a compensating
conversion, weighted 70.8% by the ramp at T=6.

**Two residuals, both accepted and both in the prime's favour:**

1. **~0.66 bps/yr — the settlement lag.** The charge accrues to month-end
   but is capitalised ~20 days into the next month, earning nothing in
   between, while Sky's SSR cost compounds continuously. Not fixable by any
   choice of units; only a shorter settlement cadence (DSC) or a non-zero
   nominal Sky spread addresses it. At a 0 bps nominal spread Sky is
   structurally short ~0.66 bps ≈ $370K/yr on ~$5.6B of prime debt.
2. **The SOFR day-count basis.** The Fed annualises SOFR on actual/360; we
   accrue on n/365, so we under-accrue it by ~1.39% of its value (~5 bps on
   the reference). Using it as published keeps the subsidy alive; converting
   to a /365-equivalent (3.6977%) would push the reference above BR_apr and
   clamp the subsidy to zero. The Atlas does not specify a basis — tracked
   in `SNR_QUESTIONS.md` as a clarification request.

**Note on thin headroom.** BR_apr (3.664456%) now sits only ~1.4 bps above
SOFR. August 2026's prints ranged 3.62%-3.66%, so the subsidy applied on
every day of the month — but the highest print (3.66%, 2026-08-25) came
within 0.45 bps of BR_apr. A further SSR cut or a SOFR uptick of half a
basis point would clamp the subsidy to zero on individual days, so
`zero_benefit` warnings become a live possibility rather than a signal of
stale data. Judge them against the day's prints before treating one as a
defect.

#### High priority (Grove Q1 — added 2026-05-02 after Grove team workbook reconciliation)
**E1 aHorRwaRLUSD off-pool yield channel.** Aave Horizon's on-chain `liquidityIndex` only grows ~0.87% APY (matches our $67K Feb 2026 revenue exactly); the remaining $447K of Grove team's $514K is **off-chain rewards accrual** (Holdings sheet `Rewards` column grew +$431K with `claimed` flat). Most likely fed from Merkl (`MERKL_DISTRIBUTOR = 0x3Ef3D8bA38EBe18DB133cEc108f4D14CE00Dd9Ae` in Grove address registry) or Aave Horizon's own RWA-fund accrual API. Until Grove confirms the canonical feed we won't integrate (mis-attribution risk). **See QUESTIONS.md G3.**

**Update 2026-05-13 — resolved on-chain via Option A (`external_alm_sources` + Cat C external-rewards path).** Dune verification (query 7489308) showed Merkl claims TO Grove ALM on **Feb 6 2026** (≈$3.78M aTokens) and **Apr 24 2026** (≈$2.39M aTokens), delivered as `aEthRLUSD` + `aHorRwaRLUSD` (not the underlying RLUSD). This is exactly BA's preferred boundary: capture rewards when they hit the ALM as a stable-ish receipt. Threaded through a new `VenueRevenue.external_revenue` field that flows to prime 100 % (NOT subject to SDE-splitting). Re-running Feb / Apr 2026 will credit E1 + E3 with the claim amounts (closes the bulk of the $447K Feb gap for E1; Apr brings the remainder). Pre-Feb-6 / mid-March accrual that was never claimed on-chain remains uncredited by design — matches BA's "boundary at ALM ingress" preference rather than mirroring Grove's spreadsheet's accrued-but-unclaimed accounting.

**Update 2026-05-14 — data source switched from Transfer to Claimed events.** Initial Option A read aToken `Transfer` events filtered by `from = Merkl distributor`. Inspection of the actual claim tx (`0x8a81d6dd…704a`) showed Merkl's Aave-integrated flow fans out across multiple intermediaries — the *real* aToken transfer to the ALM has `from = Aave pool proxy` (a different address per aToken contract), while the Merkl distributor itself moves separate static-aToken wrapper instances. The pool proxies can't be added to `external_alm_sources` without false-positiving ordinary Grove-initiated Aave deposits. New approach (`_merkl_claims_revenue_usd` + `queries/merkl_claims_ethereum.sql`): read the Merkl distributor's `Claimed(user, token, amount)` event directly — one semantic event per claim with canonical amount, independent of internal routing. The generic Transfer-based helper (`_atoken_transfer_revenue_usd`) is kept for direct-sweep channels (Anchorage interest, BUIDL yield mints) where the configured sender IS the ALM-ingress address. Dispatch via `_MERKL_DISTRIBUTORS` set in `normalize/positions.py`.

**Update 2026-05-14 (b) — Claimed-event filter requires a JOIN to the aToken `Mint` event for venue attribution.** The naïve filter `Claimed.token = venue.token.address` returns zero rows: Merkl's `Claimed.token` is the **staticAToken wrapper** (e.g. `0x72eeed80…` for aEthRLUSD, `0x503d751b…` for aHorRwaRLUSD), NOT the underlying aToken the ALM ends up holding (e.g. `0xfa82580c…` aEthRLUSD itself). Discovered via Dune debug walk on tx `0x8a81d6dd…704a`. The aToken contract emits `Mint(caller, onBehalfOf, value, …)` in the same tx alongside the staticAToken's redeem, where `caller = staticAToken` (= `Claimed.token`) and `onBehalfOf = ALM` (= `Claimed.user`). Pairing `(c.tx_hash, c.topic2) == (m.tx_hash, m.topic1) AND m.contract_address = venue.token.address` deterministically attributes each Claimed amount to its venue — even when a single tx claims rewards for multiple aTokens (the Feb 6 tx claims for BOTH aHorRwaRLUSD AND aEthRLUSD, and each Claimed pairs with exactly one Mint). The JOIN keeps `venue.token.address` (already in YAML) as the only address the operator configures; Merkl-internal addresses (staticAToken wrappers) are derived per-tx via the join, so they never enter `grove.yaml`. End-to-end verification against `_merkl_claims_revenue_usd`: Feb 2026 E1 $821,306.03 / E3 $2,963,561.64; Apr 2026 E1 $978,913.67 / E3 $1,411,897.31; grand total $6,175,678.65 — matches the Grove team's expected claim amounts to the cent. **Superseded in part by the 2026-08-05 update below: the Claimed+Mint JOIN is now "Pattern A" of a two-leg query — the "returns zero rows" observation about `Claimed.token = venue.token.address` holds for wrapper-paid campaigns only.**

**Update 2026-08-05 — Merkl direct-aToken payouts ("Pattern B"); Jul 2026 restated +$1,468,181.44.** The Jul 2026 Horizon-RLUSD campaign (funded from `0xcc6dede7…ee000` starting Apr 23) pays rewards in the aToken **directly** from the Distributor: `Claimed.token` IS the aToken (`0xe3190143…` aHorRwaRLUSD) and the payout is a plain aToken `Transfer` Distributor → ALM — no staticAToken wrapper, no redeem `Mint`, so the 2026-05-14 (b) JOIN matched zero rows and the two July claims — Jul 13 1,425,596.0044 (tx `0x0af33386…be492`) and Jul 21 42,585.4312 (tx `0xf960709c…6ec9b`, hours before Grove zeroed the E1 position) — were silently bucketed as principal inflow. `merkl_claims_ethereum.sql` now runs two **disjoint** legs: Pattern A (the wrapper JOIN, semantics unchanged — Feb/Apr amounts reverified identical to the wei) and Pattern B, which sums the **receipt Transfers** (venue aToken, Distributor → ALM) gated on a same-tx `Claimed` marker for that aToken. Using the receipt rather than `Claimed.amount` preserves the JOIN's two implicit invariants: revenue is only booked when the ALM actually received the tokens (Merkl claims are keeper-executed and support operator/alternate-recipient routing — both July claims were sent by third-party EOA `0xa2bdfaa0…`, no Grove-signed tx), and the amount is denominated in the venue aToken's own units. Leg disjointness splits on `Claimed.token` (a claim of the venue's own aToken is never a Pattern-A candidate); note the receipt-Transfer path is NOT a usable discriminator — wrapper campaigns *also* transfer the reward (wrapper) token Distributor → ALM before the in-tx redeem, so an exclusion keyed on it zeroes legitimate Feb/Apr claims (verified empirically during review). Cross-venue leakage would require a direct-paid aToken to appear as `Mint.caller` on another venue's aToken (i.e. an aToken contract calling `pool.supply`), which Aave V3 mechanics preclude; the invariant is pinned by the Jul-2026 E3 = $0 e2e row. Regenerated `settlements/grove/2026-07`: E1 revenue $117,961.80 → $1,586,143.24 (`external_revenue` $1,468,181.44, 100% prime — E1 is 0% SDE; Sky side unchanged at $8,003,550.33; no sky_total impact — paid basis). Claims grand total across 2026 now $7,643,860.09. Pinned by `tests/integration/test_merkl_claims_e2e.py` (Jul E1 ≈ $1.47M Pattern B; Jul E3 = $0 guards cross-venue leakage). Relates to QUESTIONS.md G3 (off-pool yield channel identification) — this closes the *capture* side for Merkl regardless of payout mechanic; the G3 ask (Grove confirming the canonical rewards feed) is unchanged.

**Update 2026-08-06 — Chronicle VAO consumer rotation froze E22 ACRDX NAV; all Chronicle addresses migrated to per-asset Routers; Apr–Jul 2026 restated (reconciliation item).** E22's configured feed `0x51cc9463…` was `ChronicleVAO_Centrifuge_ACRDX_Consumer_2` — a *consumer instance* that Chronicle rotated away from (they were on Consumer_7 by Aug 2026). The abandoned consumer kept answering `read()` with its last value (1.016057, last written 2026-05-07), so the fallback chain never fired and E22's NAV silently pinned: Jun + Jul 2026 booked $0 E22 revenue, and Apr/May EoM marks diverged from the canonical NAV (Apr 30: 1.017027 vs true 1.015380). Fix, three parts: **(1)** every Chronicle address in `grove.yaml` now points at the per-asset **Router** (`ChronicleVAO_<issuer>_<asset>_Router_1` — stable address that always forwards to the live consumer): E22 ACRDX → `0x87603527…`, E7 STAC → `0x802cacc1…`, E8/E20 JAAA fallback → `0x5d44916e…`, E9 JTRSY fallback → `0xe980a33e…`. Routers verified byte-identical to their consumers at every 2026 SoM/EoM pin block (STAC/JAAA/JTRSY consumers never rotated, so only E22 changes numbers; the ACRDX Router tracks the canonical Centrifuge erc4626 vault NAV within 1e-6 throughout). **(2)** `extract/oracles/chronicle.py` now prefers `readWithAge()` and logs a WARNING when the price at the queried block is older than 14 days — the freshness tripwire the incident lacked (staleness never raises; plain `read()` remains the fallback for contracts without `readWithAge`). **(3)** Apr–Jul 2026 Grove settlements re-run with the router feed — treated as a **reconciliation item** since Apr–Jun were already settled (MSC#8–#10). Supply-side deltas are confined to E22 (32.2M shares held directly at the Grove Plume ALM, no intermediary — verified on-chain): Apr −$82,374.16 (Consumer_2 overstated Apr 30), May +$127,543.10, Jun +$77,179.62, Jul +$21,697.22 — net **+$144,045.78** prime revenue across the four months; Sky-side revenue is untouched (E22 is 0% SDE). NB the restated Apr–Jun artifacts also pick up two pipeline features added after those months were last generated: the Chronicle Points demand-side component (#166 — Apr +$17,001.13, May +$18,585.45, Jun +$15,649.90) and the $0-balance E40/E41 Diamond-PAU rows — version skew from re-running on the current pipeline, not part of the oracle correction; both are called out so the reconciliation reviewer can attribute every changed line.

**Update 2026-08-06 (b) — G25 (spUSDG two-Star yield split) closed on GitHub without a recorded resolution.** Issue #161 was closed 2026-08-04 with no closing comment, so no counterparty-confirmed split methodology exists in writing. The pipeline stance is unchanged: the E39 Robinhood spUSDG venue stays a commented-out stub in `config/grove.yaml` (deployment leg not live on-chain — vault held $1.97 total as of 2026-08-03 — and booking it would double-count against Spark's books until the curator-fee vs vault-yield split is specified). If the close was intentional ("resolved elsewhere"), the split terms still need to land here before E39 activates; treat any E39 activation PR without them as blocked.

#### High priority (Spark Q1 — most resolved 2026-04-30)
1. **L2 RPC endpoints + Dune key** — *resolved*. drpc URLs for Arbitrum / Optimism / Unichain were added to `.env`. `DUNE_API_KEY` not set, but Dune access via MCP unblocked the captures.
2. **Spark `start_date` boundary** — first frob is 2024-11-18; could differ from Sky's billing anchor. Verify with Spark/Sky team. **See QUESTIONS.md S1.**
3. **PSM3 ABI** — *resolved*. Confirmed on Base + Optimism live; Arbitrum + Unichain assumed same ABI (CREATE2-deployed by Spark). Selectors `0xce7c2ac2` (`shares`) + `0x41c094e0` (`convertToAssetValue`). Implementation updated.
4. **Spark Sky Direct list** — *resolved*. Flagging S19/S20/S21/S24. PSM3 holdings handled via PSM mechanic. Anchorage = principal-sent-out.
5. **Cat A revenue methodology** — *resolved 2026-04-30*. `_cat_a_capital_inflow_timeseries` falls back to `cumulative_balance_timeseries` when both `inflow_by_counterparty` and `external_alm_sources` are empty → revenue = 0 (correct for par-stables with no off-chain yield source). Spark has empty `external_alm_sources`, so its Cat A revenue is $0.
6. **Hardcoded period detection in `spark_fixture_loader.py`** — **NEW (from review):** the loader's `eth_eom`-block branch only handles Q1 2026 (Jan/Feb/Mar). Any other month silently skips Cat A `cumulative_balance_timeseries` synthesis → revenue overstate. Fix needed before re-running for Q2+. *(Internal — tracked here, not in QUESTIONS.md, since it's our code, not a Spark-team question.)*
7. **PSM3 daily RPC error isolation** — **NEW (from review):** `_value_at` propagates exceptions per-day; one failed RPC kills the whole chain's PSM3 timeseries → utilized over-stated. Wrap with per-day try/except + log. *(Internal — tracked here, not in QUESTIONS.md.)*
8. **Residual `cof_total` gap vs Grove (+$244K Σ Jan-Apr 2026) — likely Grove-side methodology.** **NEW (2026-06-04 daily-resolved sd_share investigation):** after switching to daily-resolved sd_share + burn-day override (matches Grove's `sd_revenue` on all four months to within upstream `actual_revenue` drift), the remaining headline gap vs Grove is +$207K Σ Jan-Apr — decomposed as Σ Δ sd_revenue = −$36K (upstream Centrifuge accounting drift) and **Σ Δ cof_total = +$244K** (BR × Net_Subs over-attribution). Per-month: Jan +$60K, Feb +$101K, Mar −$42K, Apr +$125K. The over-attribution lives in the daily `utilized` × BR_rate computation in `compute_sky_revenue`, not in the SDE-split. **Two known Grove-side methodology gaps likely explain the residual:** (a) Grove may not compute the daily subsidised BR per Sky governance (our `ref_rate + (BR − ref_rate) × T/24` is correct per the governance spec — see §17.13 medium-priority item 6); (b) Grove's "Subscriptions" column appears to read only `vat.frob` events, missing the `vat.grab` events from the monthly Sky-Share spell (cumulative grab through 2026-05-11 = $57.91M — PR #103 added grab to our `cum_debt` to match `Vat.urns(ALLOCATOR-BLOOM-A).Art` exactly). Either side could explain the over-rate of ~0.046% of avg Net_Subs (small per-day, cumulative to +$244K). **Tracking via Q-G23 (QUESTIONS.md);** acknowledge the gap pending Grove team confirmation on their BR formula + Subscriptions composition. No code change planned on our side until Grove confirms.

9. **S32 sUSDS POL — compensation is the Demand Side Distribution Reward, not the agent rate (review-ack 2026-06-25).** Removed `pol_agent_rate: true` (+20bps) from Spark venue S32. The Ethereum sUSDS POL collateralises Spark Savings (V2) deposits, so the prime's compensation on it is the **Demand Side Distribution Reward** — handled outside the supply-side P&L — not a Sky→Spark agent rate. S32 is now charged full BR with `demand_side_spread` only: the 30bps `susds_spread_reimbursement` is omitted from `sky_revenue` (routed to depositors via DSDR), and there is **no** 20bps agent-rate refund. **Numerical impact:** Spark Jan–May 2026 `sky_revenue` +$888K (Jan +87.5K, Feb +90.9K, Mar +163.2K, Apr +223.9K, May +322.5K); prime agent profit falls by the same. The `pol_agent_rate` mechanism had no remaining users after this and was deleted entirely (compute / domain / config / provenance). The **subproxy** agent rate (SSR+20bps on idle USDS/sUSDS) is a separate, still-live mechanism and is unaffected. Supersedes the older "S32 = 30bps spread to prime" framing in §17.7 and the resolved-questions table below.

   **Carve-out reconciliation (on-chain verified, 2026-07, Spark reconciliation §7/§8 item 1).** S32's mainnet sUSDS holding mixes a debt-sourced slice (true POL + spUSDT/spPYUSD collateral, funded via `vat.frob`) with a **depositor-sourced spUSDC Savings-V2 slice** (PSM-routed USDC→USDS→sUSDS, no new ilk debt). `deduct_savings_v2_deployed` (a **live** RPC read of `spUSDC_V2.assetsOutstanding()`, despite the historical "no-op" name) removes the depositor slice's SSR from S32's MtM via `_savings_v2_depositor_ssr`, so the prime is credited only the debt-sourced appreciation. Jan–May 2026: booked **$10,690,274** + carved-out depositor slice **$5,713,303** = **$16,403,577** gross, matching Spark's independently-computed value-growth-net-of-inflows ($16,397,692) to **0.04%**; the $5.71M carve-out matches Spark's "$5,707,417 unrecognised" to ~0.1%. The $5.7M is owed to spUSDC depositors (the VSR liability, out of MSC scope per S30/#126), not the prime — it cannot be claimed as prime revenue while the offsetting VSR cost stays out of scope. The S32 config comment records the same figures; this is the review-ack of record.

10. **Distribution Rewards now sourced from `settle-dr-dune` (review-ack 2026-06-26).** `MonthlyPnL.distribution_rewards` is no longer a $0 placeholder — it is populated per prime/month from the `settle-dr-dune` submodule's `dr_comparison_latest.xlsx` Summary tab (per ref code), surfaced in `summary.md` (headline + "DR per ref code" table) and the xlsx. Scope: Spark / Grove / Skybase / Keel (tagged-DR primes); the untagged "Other" bucket and primes without a DR group (e.g. obex) render "TBD". Wiring in `src/settle/load/dr_rewards.py` + `write_settlement`; refresh without recompute via `run_{prime}_2026.py --dr-only`. **Numerical impact (Jan–May 2026):** prime_agent_total_revenue / prime profit rise by the DR amount (e.g. Spark ~$1.0–1.6M/mo, Grove up to ~$0.19M/mo, Skybase ~$0.23M/mo, Keel ~$0.03M/mo); `sky_revenue` is unaffected. See §17.6. Supersedes the "$0 placeholder" references in the Grove-Q15 / Spark-DR notes above.

11. **Cat A idle par-stable revenue is $0 by construction unless the venue declares `external_yield_source: true` (review-ack 2026-07-10, PR #153; supersedes the conflicting #148/#151 designs).** Methodology: a par-stable idle holding earns nothing by itself, so an UNFLAGGED Cat A venue's entire Δvalue is capital — dated by the cumulative-balance transfer scan (mid-month flows keep their real dates for CoF time-weighting; a period-start residual row absorbs any transfer-capture artifact so revenue is exactly $0). Flagged venues (Spark S26 Anchorage USDC sweeps, S28 PayPal/Paxos PYUSD rewards) run the counterparty classifier, protected by a reconciliation guard: material in-period balance movement not accounted for by the counterparty log (missing, stale, or partial capture; $1 aggregate floor; boundary-neutral transits exempt) raises instead of silently misbooking the gap as ±yield (`SETTLE_ALLOW_UNCLASSIFIED_CAT_A=<venue-ids>|1` is the explicit per-venue hatch). Config is validated at load: the flag is PAR_STABLE-only, mutually exclusive with `force_capital_inflow`, and requires a registered `external_alm_sources` entry for the venue's chain. **Numerical impact (restated):** Spark 2026-05 S27 −$194,444.44 phantom negative yield → $0 (supply-side revenue 2,768,356.15 → 2,962,800.60); Grove 2026-01 E31 +$3,688.87 / E32 −$3,595.46 and 2026-02 E31 +$6,380.03 / E32 −$2,254.27 → $0 (the E13/E32 class). Operational rule: if an external source starts paying a currently-idle venue (e.g. Anchorage sweeping into S27), the flag MUST be flipped and the venue's counterparty log captured — an unflagged venue books such inflows as capital with no warning by design.

12. **Non-venue sUSDS layer now itemised in `summary.md` (review-ack 2026-07, Spark reconciliation §8 item 2).** The Spark 2026-07 reconciliation flagged a ~$5.2M "non-venue layer" — orchestrator-level credits that carry no per-venue `actual_rev` row and were invisible in the published report. It is the **PSM3 sUSDS SSR appreciation** (`psm3_susds_appreciation`, ~$1M/mo, booked straight into `prime_agent_revenue` because PSM3 is a basket contract, not a venue) plus the sUSDS **30bps spread reimbursements** (PSM3 + Curve + the Cat B L2 proxies). `load/summary.py` now renders a "Non-venue sUSDS credits" table from the existing `MonthlyPnL` fields (`psm3_susds_appreciation`, `psm3_susds_spread`, `curve_susds_spread`, `susds_spread_reimbursement`). **Methodology note on the derived Cat B L2 row:** it is `susds_spread_reimbursement − psm3_susds_spread − curve_susds_spread` (the L2 proxies' 30bps, which also show per-venue in the `spread_reimb` column); only the positive residual is rendered — a non-positive value is definitional drift (e.g. `sky_only` zeroing components while retaining the aggregate) and is omitted. **No numerical change** — presentation only; confirms none of the layer is S32 (its 30bps is routed to DSDR, per item 9).

13. **Cat C/D aToken yield recovered for mid-window entry/exit months (review-ack 2026-07-13, PR #149).** The whole-period closed-form (`bal_eom × scaled_som / scaled_eom − bal_som`) silently returns ~$0 when a venue ENTERS mid-month (no SoM basis), EXITS to dust (degenerate denominator), or round-trips within the month — rebase earned while the position was held was misbooked as capital. For venues with NO captured mint/burn event data, a daily-EoD-grid fallback now recovers the yield: per-day closed-form segments (shared `_atoken_closed_form_seg_yield`, ROUND_HALF_EVEN), each capped at 25%-APR/365 (warns when the cap binds), clean-exit-within-a-day booked $0 (indistinguishable from an intraday deposit-drain; ≤1 day of yield lost per exit). The recovery gate is dust-aware (`scaled_som × 1000 < scaled_eom` counts as entry — Aave leaves 1 wei on exit), detects 0→0 round trips via three cached quartile probes (sub-quarter-month round trips are the documented miss), clamps the day grid to `prime.start_date` (genesis months), and stamps recovered inflows on their real calendar days for CoF time-weighting. **Numerical impact (restated):** Spark 2026-03 S9 +$162,936.93, S2 +$32,005.46 (supply-side 2,350,300.04 → 2,545,242.43); 2026-04 S9 +$467,640.35, S54 +$4,618.66 (supply-side 834,856.97 → 1,307,115.98). Venues WITH event data (Grove Horizon aTokens) are untouched by construction.

14. **OBEX debt/balance backend flipped Dune → HyperSync (review-ack 2026-07-14, envio-debt-source-spike).** New per-prime `sources:` YAML block (validated at config load: keys `debt`/`balance`/`ssr`/`position_balance`/`block_resolver`, values must be registered backends; the override merges into ANY entry point's `Sources` for fields left `None` — runners, CLI, and snapshot all resolve the same backend). `HyperSyncDebtSource` reads the Vat's anonymous frob/grab `LogNote`s via raw topic0 filtering (HyperIndex can't decode 4-indexed anonymous events, enviodev/hyperindex#990), routed through the shared reorg-safe `hypersync_store` (fetch-once, Postgres-cached, never persists inside the reorg window; coverage never claims unfetched gaps). Parity with `DuneDebtSource` semantics: per-call `block_date >= start_date` filter (no process-wide env floor), exact integer-wad aggregation in Python ints (immune to int64 wraparound), ÷1e18 once under a 60-digit Decimal context, and **fail-loud completeness** — a pin block beyond HyperSync's archive height raises instead of silently truncating the tail of the month. **Numerical impact: none** — validated dart-for-dart against Dune over 544 days (spark/grove/obex ilks) before the flip; `envio` (HyperIndex GraphQL) remains registered as a comparison-only backend, not wired to any prime.

15. **Tier-2 HyperSync migration + spark June chi/EoD restatement (review-ack 2026-07-19, PR #154).** (a) `position_balance: hypersync` enabled for obex, keel and skybase: position balances now come from a self-verifying event/RPC hybrid — a token is served from Transfer-log sums only after Σ(Transfer) == RPC `balanceOf` at TWO distant blocks AND a structural aToken probe (`POOL()`/`UNDERLYING_ASSET_ADDRESS()`) rules out rebasing; the probe FAILS CLOSED (a transport error keeps the token on the always-correct RPC path). Block anchors resolve off HyperSync (exact returnable head; head-clamped resolutions are never cached — the #156 poisoned-EoD class). **Numerical impact: none** — the full-fleet run reproduces all committed reports byte-identically. (b) Spark 2026-06 restated **+$6,357.21** supply-side (2,334,896.25 → 2,341,253.46): the #156 poisoned June-30 EoD cache entry (21:10 UTC instead of 23:59:59) had fed the cross-chain chi/daily-EoD reads valuing the L2 sUSDS POL proxies (S37 +2,410.21, S43 +1,643.51, S47 +2,292.23, S51 +11.27); regenerated with the entry purged. Per-prime `sources:` overrides now apply on EVERY entry point (production runners, live runner, sync_raw_data) with truthful provenance labels (`resolved_source_labels`), and runners fail fast when a hypersync-flagged prime is missing `ENVIO_API_TOKEN`.

16. **New reporting unit `non_msc` — Sky protocol P&L outside the prime-agent perimeter (review-ack 2026-07-15, feat/non-msc-report).** Sixth unit next to the five primes (NOT a `Prime`: no ilk/ALM/BR/CoF machinery): `config/non_msc.yaml`, one Dune execution per month (`queries/non_msc_streams.sql`), artifacts under `settlements/non_msc/`. Methodology per the Sky reconciliation note (hackmd.io/@W57nO5PyRMKhcLqjvsLifw/S1zxTDpXMg): **income** = PSM/Coinbase LitePSM-jar burns, cash-recognized per the doc's literal rule — the FIRST jar burn after a month ends is that month's income; an extra burn in the same window (e.g. 2026-01-08 after 2026-01-02) is surfaced with a warning but NOT attributed, pending the methodology author's confirmation + stability fees on the 9 core-vault ilks as `Art × Δrate` at each `vat.fold` (Art = running frob+grab dart sum since genesis — the vow's own recognition); **expense** = savings interest at `drip`: sUSDS GROSS across all holders (the SSR on prime-held sUSDS stays in the expense — MSC `sky_revenue` carries the offsetting BR income per Rule 5, so a carve-out would double-count at the consolidated level; the prime/user split — Σ_days shares_EOD(d−1) × Δchi(d) over L1 ALM/subproxy + L2 ALM/PSM3 holders — is rendered as an informational breakdown, cross-checked to 0.07% against `psm3_susds_appreciation`) + legacy DSR (`vat.suck` to the pot) + stUSDS. The consolidated `sky_total` headline = Σ prime sky revenue − prime demand-side payments (agent rate + DR) + non-MSC net; May 2026 = 14,989,029 vs the methodology post's 14,896,511, fully decomposed (+202,429 RWA002-A in scope / −199,232 restated-vs-published MSC leg / +90,484 demand-side definition / −1,162 their Method-B expense). **Validation (May 2026):** every gross line reproduces the reconciliation note to the dollar (PSM 10,644,203; fees 4,476,785; sUSDS 18,107,793; DSR 249,021; stUSDS 1,061,298); prime carve-out 5,799,597 (spark ALM ≈ all of it); net +1,502,472. Follow-up (2026-07-15, same branch): the stability-fee scope was widened from the 9 core ilks to the FULL non-ALLOCATOR ilk universe from `vat.init` (adds RWA002-A ~$200K/mo; future ilks auto-included), and the prime carve-out gained the L2 leg — prime L2 sUSDS (Spark ALM proxies + PSM3 reserves on Base/Arbitrum/Optimism/Unichain) × the same global Δchi, cut by calendar date since L2 block numbering differs from the L1 pin. Remaining known items: (b) RESOLVED on-chain 2026-07-15 — the jar pays one payment/month (same sender, burned same day, days 2-14 of the following month); the December 2025 slot is empty and January 2026 has two burns, so Jan 2 ($9,618,049) is November's LATE payment and Jan 8 ($11,046,890) is December's on-slot payment (cadence + smooth amount progression + identical sender/pattern). The doc's literal first-burn rule would mis-assign both Nov'25 and Dec'25; 2026 months are unaffected (one burn per window). If the report extends back to 2025, use sequential burn-to-month matching; (c) BA Labs `info-sky.blockanalitica.com/financials/settlements/historic/` is the REFERENCE series for the eventual consolidated total (Σ prime sky revenue + non_msc net) — reference only, never blended into our numbers.

17. **Spark subsidy reference rate moved EFFR → 3M T-Bill; EFFR deleted from the codebase (2026-07-30, PR `feat/spark-tbill-subsidy`).** Resolves the QUESTIONS.md **S5** contradiction in favour of Atlas A.2.8.2.2.2.2.2, which names `t-bill_rate` for *both* primes and never mentions EFFR; the opposing 2026-05-02 governance note (reaffirmed by Spark on 2026-05-06) is superseded. `config/spark.yaml` now sets `ref_rate_kind: tbill_3m`, the `effr_apy` column is removed from `config/subsidy_reference_rates.yaml`, and `_VALID_REF_RATE_KINDS` is `('tbill_3m',)` — the field survives so a future per-prime divergence needs no schema change, but no second series exists. Spark also moves to `sources: {debt: hypersync}` so the BR charge is priced off an independently sourced ilk-debt timeseries (same family switch as item 14). **Direction:** the 3M T-Bill sat ABOVE EFFR for most of H1 2026 (and 18bps above through June, 3.78→3.87% vs 3.62–3.63%), so the subsidy narrows, Sky charges Spark MORE and Spark's supply-side revenue falls by the same amount. **Measured impact, Jan–Jun 2026: Spark `sky_revenue` +$304,476**, Spark supply-side −$304,476 (zero-sum; demand side unchanged). The HyperSync debt swap contributes **$0** — a rate-only replay holding the Dune-sourced `utilized` fixed predicted the pre-correction total to the dollar, so HyperSync's ilk debt reproduces Dune's to the cent for Spark Jan–Jun. Reproducing MSC#5–#10 as published requires checking out a commit before this one.

18. **3M T-Bill series rebuilt from treasury.gov; March 2026 was wrong and January unverifiable (2026-07-31, same PR).** Found while migrating Spark off EFFR. `config/subsidy_reference_rates.yaml` now carries one row per published business day (146 rows, `2025-12-31 … 2026-07-30`), every value reconciling 1:1 to the **daily yield curve `3 Mo`** column — *not* the separate "daily treasury bill rates" file, whose 13-week bank-discount / coupon-equivalent quotes differ by 8–15 bps and will not reconcile. Two defects fixed: (a) **March carried five hand-entered rows** describing a smooth 3.66%→3.58% decline when actual prints were flat at 3.71–3.74% — every March value understated the rate by 6–14 bps, overstating both primes' subsidy; (b) **January carried two rows**, both 3.67%, both on non-trading days (Jan 1 holiday, Jan 31 Saturday) — the whole month was an unverifiable carry-forward against real prints of 3.62–3.71%. Feb/Apr/May/Jun already matched the source and are unchanged to the cent (verified: Feb moves $0.00 for both primes). Max carry-forward is now 4 days. **Impact — this is a data correction to already-settled figures, independent of the EFFR→T-Bill switch:** Spark +$67,770 on top of the rate change; **Grove +$65,701 with no methodology change at all** (Grove was already `tbill_3m`), of which +$65,661 is March. Grove Jan–Jun regenerated in the same PR.

#### Medium priority (affect numerical accuracy)
5. **Reconciliation gap with Sky's reported Sky Share for Grove** (~$1.13M for Mar 2026 under the pre-subsidy model). Largely closed by 2026-05-02 work (subsidy + SDE refactor + pricePerShareFeed NAV); Feb 2026 residual is now ~$45K excluding the E1 Horizon rewards channel. **Need:** Sky to confirm whether Asset Value definition for BR_charge differs from `subscription − SDE_value` time-weighted (the formula we now match per Grove team's workbook).
6. **Subsidised rate ramp** — *resolved 2026-05-02*. Implemented per Sky governance: program_start 2026-01-01, T = months elapsed, formula `ref_rate + (BR − ref_rate) × T/24`, cap at first $1B utilized. Every prime uses the 3M T-Bill (Spark migrated off EFFR 2026-07-30, see item 14). Daily rates carried in `config/subsidy_reference_rates.yaml`.
7. **Sky Direct exposure list — automation** — manual diff against `sky-ecosystem/next-gen-atlas` is the current process. As the list grows beyond Treasury Bills + PSM3 + Spark Curve, this becomes load-bearing.
8. **Chronicle adapter robustness** — silently falls back to const_one ($1) when oracle returns 0x. Mitigated by `nav_overrides` fixture but the adapter itself should be tightened (distinguish "pre-deployment" from "real $1"). **Partially resolved 2026-05-13:** E22 ACRDX now uses `erc4626_vault` fallback (`0x74A739EA1Dc67c5a0179ebad665D1D3c4b80B712` on Ethereum) instead of `const_one`. Vault probe confirmed real NAV at key blocks: $1.014647 at block 24,050,000; $1.01877 at the deposit block 24,136,052 (vs. $1.00 par used previously); vault returns zero/empty at and before block 24,017,764 (pre-funding), which the dispatcher catches and falls through correctly. Remaining gap: other Cat E venues still use `const_one` as ultimate fallback; a general "refuse $1 when NAV is far from $1" guard is not yet implemented.

#### BA Labs call #1 — methodology + documentation

First Q&A session with the BA Labs team reviewing PnL calculations for Spark on Oct 2025 and the `blockanalitica/sky` repo (commit `4eda36a`). Captured here as: methodology resolutions (apply directly), documentation cross-references (existing PRD coverage), and follow-up open questions (tracked in `QUESTIONS.md` as `B7`, `B8`, `B9`, `B10`, `B13` — note `B12` is intentionally unused).

**Methodology resolutions** (apply directly — no further BA confirmation needed):

- **Offchain-transfer accounting (call item 1).** BA confirmed the prime balance-sheet approach: total NAV is computed from assets at the ALM Proxy. Offchain transfers (Anchorage interest sweeps, Merkl rewards, BUIDL yield mints) reach the ALM Proxy as ordinary token transfers and are picked up by the existing `external_alm_sources` allowlist on Cat A par-stable accounting (PRD §17.5). **Operational requirement BA flagged**: prime agents should swap volatile reward tokens to a stable asset *before* they hit the ALM Proxy (otherwise the par-stable accounting double-prices). This is a prime-side discipline, not pipeline code.
- **LP token PnL — split into constituents, event-by-event (call item 3).** For Curve / AMM venues holding USDS + a counterpart token, BA's recommended methodology is to split the LP into its constituent tokens and account for inflows event-by-event (rather than `balance_of(LP) × virtual_price`, which doesn't separate the idle USDS leg from the actively earning leg). Today our `_curve_lp_index_weighted_inflow` (PRD §17.4 Cat F) uses RPC reserves × underlying prices (POC Method B) which captures total LP value but NOT the idle/active split per leg. Worth revisiting when an LP venue has a meaningful idle USDS share — Spark S24 (sUSDS/USDT) and S25 (PYUSD/USDS) are candidates. Tracked as an internal TODO; opening as a question to BA isn't needed since the methodology direction is clear.
- **"Snapshot" terminology in BA's code (call item 6).** BA flagged a misnomer: in `blockanalitica/sky`, "snapshot" does NOT mean `balanceOf(token, holder)` at a moment in time — it means **the output of a daily calculation**. Different concept from our own `Snapshot` module (in `src/settle/snapshot/`, which IS a point-in-time balance sheet). Worth being explicit about this when reading BA's source for cross-checks. No code change needed in our pipeline; awareness only.

**Documentation cross-reference table** ("Elements to document properly" from the call):

| Topic | PRD coverage | Status |
|---|---|---|
| Idle USDS/DAI in lending markets | §17.7 (deferred entry); §17.12 covers the Spark side via `alm_idle = alm_share × protocol_idle_amount` | ✅ covered for Spark; Grove section can absorb the same pattern when needed |
| Idle USDS/DAI in AMM pools | §17.4 Cat F (LP) — `_curve_lp_index_weighted_inflow` via RPC reserves; **idle/active split per leg NOT yet implemented** | ⚠️ partial — see "LP token PnL" methodology resolution above; new internal TODO |
| Aave / SparkLend / Morpho position values + revenues | §17.4 Cat C (`_atoken_index_weighted_inflow` — `scaledBalanceOf × liquidityIndex`); Cat B 4626 (`shares × convertToAssets`) | ✅ fully covered |
| Offchain-transfer communication from primes | §17.5 + §17.12 (`external_alm_sources` + `principal_return_overrides`); BA's "swap volatile to stable before ALM Proxy" note above | ✅ pipeline side covered; prime-side discipline is operational guidance, not code |
| Realised gains mid-month — should the prime repay debt to Sky? | The full-PnL approach we're adopting (assets + liabilities + revenue, with `Vat.ilks.Art × rate` covering the borrow side end-to-end) means realised gains accrue to the prime balance sheet without needing an intra-month debt-repayment step. sUSDS in allocation modules (e.g. the L2 sUSDS POL proxies S37/S43/S47/S51) is a Cat B venue on the asset side but earns **spread-only revenue** (30 bps) — SSR is returned to Sky via the BR charge on utilized; the prime does not pocket it as revenue. (S32 is the `demand_side_spread` special case — its 30bps is routed to Spark Savings depositors via DSDR, not earned by the prime; see §17.13 Spark item 9.) Subproxy sUSDS earns only the agent rate; it is not netted out of `utilized`. | ✅ resolved internally; no QUESTIONS.md entry needed |

**Open follow-ups** (`QUESTIONS.md`):

| Q-ID | Class | Priority | Why open |
|---|---|---|---|
| **B7** | partially answered | P2 | Need the actual list of historical offchain transfers from Miha. Operational, not methodology-blocking. |
| **B8** | partially answered | P2 | Confirm the enumeration of "edge case" venues (USDe, Superstate, BUIDL) is exhaustive. |
| **B9** | partially answered | P2 | BA's Aave/SparkLend method is `accrued × (1 − utilization) × BR`; "improve via events, or live with the small differences?" |
| **B10** | unanswered | P1 | Why does `Sky + Prime ≠ Total` for USDe / Superstate / BUIDL? Currently flagged as "manual calculations per private deal"; we need to know if MSC should replicate the manual math or treat these as audit exceptions. |
| **B13** | unanswered | P1 | SDE — (a) canonical list + Atlas version, AND (b) settlement-semantics confirmation: the Nov-2025 Atlas edit says Sky-takes-all on SDEs, but `laniakea-docs/accounting/prime-settlement-methodology.md` Step 4 still leaves the prime with the surplus-over-BR. Need definitive answer; our MSC behaviour matches Sky-takes-all. |

#### BA Labs call #2 — methodology + documentation

Second Q&A session with the BA Labs team — a 12-question review of the existing-venue accounting methodology. Source: BA's `ba_review_2` doc ("Questions to BA Labs #2: Review of existing venues to allocate"). Captured here in the same shape as call #1: methodology resolutions (apply directly), partial answers that update existing open questions, and new open questions (tracked in `QUESTIONS.md`).

**Methodology resolutions** (apply directly — no further BA confirmation needed):

- **NAV is the canonical framework over PnL (Q8).** BA confirmed the choice: revenue recognition is driven by NAV deltas (period-end value − period-start value − inflows), not by event-by-event PnL aggregation. Our compute layer is already aligned (see `compute/monthly_pnl.py` — `actual_revenue = (value_eom − value_som) − period_inflow` per `VenueRevenue`).
- **Principal is excluded from Prime Agent revenue under the NAV approach (Q3).** BA's clean rule: "no [principal in Prime Agent calc], as long as we are using NAV." Already aligned — `prime_agent_revenue` sums the NAV-based `VenueRevenue.revenue` field across venues, never the gross principal.
- **Non-yielding stablecoins (RLUSD, USDC, AUSD) are tracked as part of NAV (Q4).** BA confirmed: even venues with no direct yield must contribute to NAV. Already aligned — Cat A par-stable accounting in PRD §17.5 tracks every idle holding regardless of whether it generates revenue, so positions like RLUSD raw / USDC raw / AUSD raw on the asset side cleanly anchor NAV even when their own contribution to `prime_agent_revenue` is $0.
- **Multi-position rewards (Curve, Morpho, Uni v3/v4) — capture at ALM, no per-venue assignment (Q5).** BA's clean rule: "particular position assignment doesn't matter, only effect on NAV." This extends call #1's `external_alm_sources` finding to multi-position venues — when a single reward stream funds rewards traceable to several venues (e.g. AUSD rewards across Grove's Curve / V3 / Morpho positions, AVAX rewards across Spark's Avalanche venues), MSC should NOT try to apportion per-venue. Capture once at the ALM Proxy ingress and let the NAV reflect the aggregate. Today our pipeline is already ALM-centric for these flows; the resolution is to NOT add per-venue attribution complexity even when the reward source is technically known.
- **Volatile reward tokens (ETH, AVAX, MORPHO) — counted at ALM-deposit boundary, post-swap (Q6).** Restates the call #1 operational rule: primes must swap volatile reward tokens to a stable asset BEFORE they hit the ALM Proxy. MSC then captures them as ordinary par-stable inflows under Cat A. Pipeline already aligned; this is prime-side discipline, not pipeline code.
- **Galaxy / Anchorage with no yield API — wait for ALM arrival, no API-based principal-only calc (Q11).** BA's rule: when a venue has principal data but no yield feed, do NOT estimate accruals. Wait until yield reaches the ALM and recognise it then. Anchorage S23 is already implemented this way (escrow + monthly USDC sweeps via `external_alm_sources`); the same pattern applies forward to Galaxy CLO (GACLO-1) and any future API-incomplete venue. Recognition lag is acceptable; estimation drift is not.
- **In-transition assets need DAILY NAV accounting (Q9).** BA's instruction: assets in escrow / RWA withdrawal / cross-chain bridge transit must contribute to NAV at daily granularity, not aggregated weekly/monthly. Examples flagged: Centrifuge subscription/redemption queues, Anchorage escrow disbursements, bridge transactions in flight. Pipeline check: Centrifuge ✅ (pricePerShareFeed read daily for E8 JAAA / E9 JTRSY), Anchorage S23 ✅ (escrow tracked as a Cat E venue with daily reads). Bridge-in-transit — not currently a tracked state for any venue (no L2-bridge transitions cross EoM in Q1 2026 fixtures); flag for verification before Q2+.

**Partial answers that update existing open questions:**

- **Q1+Q2 → updates B11 (idle-asset / agent-rate accounting).** BA: "idle assets count only Sky side" (and "same handling" for sUSDS). This may resolve B11 if BA's "idle" means ALM-side only (no conflict, our `agent_rate` is subproxy-side per the methodology doc). It would create a real divergence if BA's "idle" also covers subproxy-side holdings, in which case BA's Prime Agent revenue is structurally `agent_rate` smaller than ours. B11 sharpened to ask BA for explicit (a)/(b) disambiguation.
- **Q7 → updates G3 (Grove Merkl / off-pool yield).** BA: historical Merkl reconstruction is "not possible" — payment token varies (sometimes volatile), reporting inconsistent. Same handling as Q6 (ALM-deposit boundary). Doesn't resolve G3 (Grove still needs to identify the canonical feed for the `Rewards` column going forward), but lowers expectations for any historical back-fill from Merkl directly.
- **Q12 → updates S14 (Spark sparkPrimeUSDC1 / Arkis).** BA flagged that they don't currently have direct Arkis API access — Arkis exposes total position value but BA needs Spark to facilitate. Implication: BA isn't an independent reference for this venue. The on-chain `convertToAssets()` vs Arkis-API authority question still needs to come from Spark / Arkis directly.

**Open follow-ups** (`QUESTIONS.md`):

| Q-ID | Class | Priority | Why open |
|---|---|---|---|
| **B11** | partially answered | P2 | Q1/Q2 ambiguous on whether "idle" includes subproxy-side. Sharpened to ask: did BA's Q1 2026 Prime Agent revenue include the SSR+20bps (USDS) / 20bps-on-cost-basis (sUSDS) component on the SLL subproxy, or skip it? |
| **B14** | unanswered | P1 | Gain-realization double-counting (Q10). BA acknowledged the risk but punted to "Atlas changes/rules possibly needed here, delay of realising gains." Material whenever a Cat E (RWA NAV) / Cat F (LP) position is unwound mid-cycle; not yet observed in Q1 2026. |

#### Grove team interview (2026-05-06) — methodology + documentation

Q&A session with the Grove team covering historical accounting, edge-case venues, distribution-reward eligibility, and the subsidy ramp. Captured here in the same shape as the BA Labs calls.

**Methodology resolutions** (apply directly):

- **Galaxy CLO — onchain USDC payment on the 10th of each month.** Grove's Galaxy CLO position pays out via on-chain USDC transfers to the ALM Proxy on or around the 10th of each calendar month. Pipeline action: ensure the Galaxy payer address is registered in `config/grove.yaml::external_alm_sources` so the monthly sweep is captured as Cat A par-stable revenue, mirroring the Anchorage S23 pattern. Aligns with BA call #2 Q11 ("wait for it to hit ALM") for venues without a yield API.
- **FundingMorpho contract is DR-eligible for Grove.** A FundingMorpho contract has been added to Grove's distribution-rewards source list alongside the legacy ref-code mechanism. Pipeline impact: `MonthlyPnL.distribution_rewards` is now sourced from `settle-dr-dune` per ref code (§17.6); confirm Grove's FundingMorpho ref code(s) appear in that reconstruction. Read semantics — see QUESTIONS.md **G20**.
- **Volatile tokens at the subproxy are skipped** (re-confirmation). Example: AVAX held in an Avalanche subproxy. Aligns with BA call #2 Q6 (count at the ALM-as-stable boundary). No code change today; relevant when Grove allocates on Avalanche.
- **BUIDL hits the ALM Proxy daily** (re-confirmation). Already handled by the `min_transfer_amount_usd: 1000000` bimodal filter on E10 BUIDL.
- **Subsidy ramp — 3M T-Bill, start 2026-01-01** (re-confirmation, partial answer to G5/B15). Grove confirmed the 3M tenor and 2026-01-01 anchor. Side note from the interview — "send value calculations for 3-month windows" — leaves open whether the subsidy itself is settled in 3-month buckets vs. the daily compound MSC currently uses; sampling frequency for the rate (daily / monthly / one-shot snapshot) is also still unresolved with BA / Sky. See **G5** + **B15**.
- **TGE status — Spark completed before the 2025-07-01 deadline; Grove has not** as of 2026-05-06. Reaffirms the framing in **B16**; penalty calculation methodology still open with BA.

**Open follow-ups** (`QUESTIONS.md`):

| Q-ID | Class | Priority | Why open |
|---|---|---|---|
| **G19** | unanswered | P1 | Agora — 8% on deployed AUSD split between native yield and an undefined secondary component. Likely affects E11 / E12; if the second component is being paid out and we're not capturing it, Grove `prime_agent_revenue` is under-counted today. |
| **G20** | unanswered | P1 | FundingMorpho DR feed — contract address + read semantics required to move Grove's `distribution_rewards` from $0 placeholder to a real number. |
| **G18** | unanswered | P2 | E8 JAAA / E9 JTRSY Jan 1 dates don't match Atlas — Rune confirmed Atlas authoritative; reconciliation pending. May shift SDE-flag windows. |
| **G17** | unanswered | P3 | Historical "lesser of (debt to Sky, NAV)" payment pattern noted by Grove for pre-MSC months. Informational; only relevant for historical reconciliation, not the forward MSC cycle. |

#### Spark team interview (2026-05-06) — methodology + documentation

Q&A session with the Spark team covering distribution rewards, SparkLend reserve mechanics, the par-stable yield-source allowlist, and the subsidy reference-rate dispute. Captured here in the same shape as the BA Labs calls and Grove interview.

**Methodology resolutions** (apply directly):

- **SparkLend reserve factor — 10% of yield, kept at protocol level.** Spark stated "10% of the yield goes to reserve factor" on USDS supply to SparkLend; recurrent reserve-factor actions are executed via spells. **MSC reading: this is SparkLend protocol income, not Spark Prime Agent income.** The supply rate Spark receives on its spTokens (S1 spUSDS / S3 spUSDT / S4 spDAI / S5 spPYUSD) is already net of the reserve factor (Aave-style: `supply_rate = borrow_rate × utilization × (1 − reserve_factor)`), so MSC's existing Cat C `scaledBalanceOf × liquidityIndex` accounting captures the prime-agent share correctly without needing to add the 10% back in. Confirmation pending — see QUESTIONS.md **S19**.
- **Distribution rewards — Cowswap (ref code 1003) + Spark in-range codes (100–999).** Two distribution-rewards streams identified: a Cowswap program at code 1003 (outside Spark's normal range) and Spark's own in-range codes. `MonthlyPnL.distribution_rewards` is now sourced from `settle-dr-dune` per ref code (§17.6); confirm Spark's Cowswap (1003) + in-range (100–999) codes are captured there. Read semantics — see QUESTIONS.md **S21**.
- **PYUSD multi-venue holdings reaffirmed** — PYUSD held simultaneously as SparkLend reserve (S5 spPYUSD), in the PYUSDUSDS Curve pool (S25), and as raw at the Eth ALM (S28). Direct transfer pathway to ALM Proxy confirmed. No new fact — reaffirms our existing config split.
- **Anchorage S23 reaffirmation** — Anchorage publishes a "principal amount allocated" API on its side; yield arrives on-chain at the SLL ALM as USDC sweeps (already implemented — escrow `0x49506C3Aa028693458d6eE816b2EC28522946872`, `external_alm_sources` capture). Anchorage charges fees pre-sweep — fee structure not detailed, see QUESTIONS.md **S22** (low priority, doesn't shift numbers since net-of-fee is what MSC sees).
- **AVAX → USDC pre-ALM swap (re-confirmation).** Aligns with BA call #2 Q6 + Grove interview. No code change.
- **Two-tier rate timeline (re-confirmation).** Spark confirmed the same timeline already in our config: **base rate** Jul 1–Dec 31, 2025; **subsidised rate** from Jan 1, 2026 onwards. Spark's stated reference rate for the subsidy is **EFFR** (linking `newyorkfed.org/markets/reference-rates/effr`). This **reaffirms current Spark config** but **stacks against Atlas's "t-bill_rate" text** — see QUESTIONS.md **S5**, now confirmed as a real disagreement (not a documentation typo) requiring Sky / BA arbitration.
- **fsUSDS dormant** — Spark confirmed the previous 100k USDS Fluid Savings position is closed; matches our existing read ($0 across S17 / S36 / S42). Already covered in §17.13 S12 note.

**Partial answers that update existing open questions:**

- **→ S2 (par-stable yield sources allowlist).** Spark confirmed "some venues send gains directly to the ALM Proxy" beyond just Anchorage, i.e. the current single-address `external_alm_sources` is almost certainly incomplete. Sharpened ask: enumerate the venues + sender addresses so we can extend the allowlist.
- **→ S5 (subsidy reference rate).** Spark reaffirmed EFFR — confirmed as a real disagreement with Atlas (see above).
- **→ B14 (gain-realisation double-counting).** Spark surfaced a related-but-distinct question — "Pay BR on realised gains?" — which became its own open follow-up as **B17** rather than folding into B14.

**Open follow-ups** (`QUESTIONS.md`):

| Q-ID | Class | Priority | Why open |
|---|---|---|---|
| **S21** | unanswered | P1 | Cowswap (1003) + in-range Spark codes (100–999) — DR feed details required to move Spark `distribution_rewards` from $0 placeholder to a real number. |
| **B17** | unanswered | P1 | "BR on realised gains" — when a NAV gain materialises into USDS at the ALM, does Sky charge BR on the enlarged USDS position? Sibling to B14; load-bearing the first quarter with significant Cat E / Cat F realisations. |
| **S19** | unanswered | P2 | Confirm 10% reserve factor stays at SparkLend protocol level (matches our reading + current pipeline). If it flows back to the prime, MSC under-counts ~10% × yield across S1/S3/S4/S5. |
| **S20** | unanswered | P2 | SparkLend "large positions trigger negative returns" — mechanism + threshold. Operators-context, not a numerical correction. |
| **S22** | unanswered | P3 | Anchorage fee structure — operational only, doesn't shift numbers (sweeps arrive net-of-fee). |

#### Code-review acks (2026-05-04 — two-reviewer pass)

Two parallel full-codebase reviews on 2026-05-04. Material findings have been fixed; the items below are intentional trade-offs documented for future maintainers.

**Fixed:**
- **Layer violation in `_curve_lp_unit_price`** — Curve yield-bearing-coin branch now routes through `IConvertToAssetsSource` (test mocks honored) instead of importing `extract.rpc` directly.
- **Stale `br_charge` sentinels in acceptance scripts** — `scripts/run_grove_2026_q1.py` and `run_spark_2026_q1_full.py` now check `vr.sd_share > 0` (the post-refactor SDE flag) instead of the always-zero legacy `vr.br_charge`.
- **Loud-warning on RPC silent-zero** — `balance_of` and `scaled_balance_of` now `logging.warning` when retries exhaust before returning 0, so a transient RPC outage stops being silently indistinguishable from a non-existent contract.
- **Deprecated `scripts/run_spark_2026_q1.py`** — guards at module-load with a `sys.exit(2)` and a clear "use run_spark_2026_q1_full.py" message; older script used a linearly-interpolated PSM3 timeseries that drifts ~$11K/mo.
- **USDS Unichain address bug (2026-05-04 review)** — `USDS_BY_CHAIN[Chain.UNICHAIN]` in `src/settle/snapshot/compute.py` was set to `0x078d…7ad6`, which on-chain probe confirmed is **USDC**. Same address was correctly under `USDC_BY_CHAIN[Chain.UNICHAIN]`. Fixed to the canonical USDS bridge `0x7e10036acc4b56d4dfca3b77810356ce52313f9c` (matches `config/spark.yaml` S52). No live impact prior to fix because Spark's Unichain subproxy isn't a USDS holding-point under the current configuration; would have been a bug the moment a subproxy USDS balance accrued there.
- **`/1e27` typo in snapshot debt-formula comment** — `src/settle/snapshot/types.py` and the header of `src/settle/snapshot/compute.py` said "Vat.ilks…rate / 1e27" while the code correctly does `/1e45` (rad scaling). Comments updated.
- **Snapshot → compute layer violation (PRD §4)** — `compute_snapshot` was lazily importing `compute.monthly_pnl.Sources` to materialize a default. Replaced with a local `_DefaultSources` carrier (same field shape, duck-typed compatible). Snapshot is now a clean peer of compute (no inbound `compute` imports).
- **Block-resolver silent drop in `compute_snapshot`** — when chain-block resolution failed, every venue on that chain was zeroed (note=`"no pin_block for chain"`) and the zeros were silently summed into `venues_total_usd`. Now emits a `_log.warning` listing all chains that lost their pin block, so operators see the failure rather than misinterpret the zero.
- **`_dune_get` poll-loop opaque 429s** — `tests/integration/test_spark_dune_parity.py:_dune_get` raised raw `urllib.error.HTTPError` on rate-limit, masking the actual code/body. Now wrapped to surface `(code, body[:200])` like `_dune_post`.
- **`KNOWN_NAV_DIVERGENCES` whitelist policy** — `tests/integration/test_ba_parity.py` now carries an explicit inclusion-policy docstring (criteria, audit sign-off, QUESTIONS.md cross-link requirement) so future additions can't slip in silently.
- **Inline Dune client justification** — added an explicit comment on `tests/integration/test_spark_dune_parity.py` explaining why the inline urllib client exists rather than reusing `extract.dune` (production targets stored-query reads; this test needs the temp-create / execute / archive flow). Documents the duplication as a deliberate, scoped trade-off pending a `extract.dune.execute_inline_sql` helper.
- **PRD §17.12.1 `alm_idle` row clarity** — table cell now says "field not yet on `Snapshot.types.VenueSnapshot`" rather than "applies to Grove but not implemented", so a reader knows the gap is a missing dataclass field, not a missing code path.
- **PRD §17.12 `result_*` table count framing** — opening sentence now scopes the catalog as the "settlement-relevant subset (~36 named tables)" instead of claiming a 44-table inventory the table below didn't reconcile with.
- **B1 reverse-engineering — PSM3 / ALM-raw layer surfaced (2026-05-04)** — the catalog row for `result_spark_usds_s_usds_usdc_in_psm_3_curve_psm_3_proxy_foundation_aave` was misleadingly tagged "PSM3 holdings (resolved earlier)". Re-probed against latest dt: the table covers raw token balances at PSM3 (per chain), the SLL ALM Proxy itself (per chain, ~$4.32B total dominated by ETH ALM sUSDS = our S32 sUSDS POL), the Curve PSM3 proxy, and Foundation. Together with `Σ alm_idle` from the per-protocol tables it forms the full pre-aggregation layer behind BA's `idle_assets` / `liabilities` / per-venue `/allocations` fields. Catalog row rewritten with the actual breakdown; QUESTIONS.md B1 refined to ask BA which `(protocol_name, token_symbol)` rows from this table feed `idle_assets` vs. get folded into other fields.
- **Anchorage S23 — earlier "no on-chain footprint" claim was wrong (corrected 2026-05-05)** — the 2026-05-01 investigation incorrectly concluded "no $150M USDC outflow ever leaves the Spark Eth ALM toward Anchorage on-chain." Re-verification on 2026-05-05 found the complete on-chain trail: SLL `0x1601843c…347e` → Anchorage Spark escrow `0x49506C3Aa028693458d6eE816b2EC28522946872` (Dec 15-19, 2025 disbursement) → holding wallet `0x8149c53ea54de2a62c9e4caef29478f1af4c7bd3` (received exactly $150M in 4 transfers Dec 18-19), then monthly interest sweeps escrow→SLL ($891,780 Jan, $891,780 Feb, $805,479 Mar, $891,780 May 4 — ≈ 7.13% APR). The prior conclusion failed because it only checked `balanceOf` at one block and saw $0 (Anchorage swaps the principal to off-chain BTC for the strategy), missing the flow trail. PRD §17.12 Anchorage entry rewritten with the corrected accounting; QUESTIONS.md S3 refined to ask only for confirmation that these two addresses are canonical (no off-chain feed needed).
- **Anchorage S23 — interest-capture wired up (2026-05-05, partial close on issue #17)** — added the Anchorage Spark escrow EOA to Spark's `external_alm_sources` so monthly USDC sweeps from the escrow land on **S26** USDC raw (ALM idle) and flow through Cat A par-stable accounting into `prime_agent_revenue`. To prevent principal-correction events from being mis-classified as yield (the $5M USDC return on 2025-12-19, and the future ≈$150M loan-termination return on 2026-06-16), introduced a new `principal_return_overrides: {chain: {address: [(date, amount)]}}` config block + classifier hook (`PrincipalReturnOverride` domain type + `_cat_a_capital_inflow_timeseries` ``principal_return_overrides=`` parameter). S23 itself was reshaped from a placeholder (`token: ANCHORAGE` EOA + `skip: true`) to a clean Cat E venue tracking USDC at the escrow — a near-instantaneous pass-through that nets to ≈ $0 over any settlement period, so its own contribution is $0 (correct, no double-count with S26) without needing a `skip` flag. Net effect for Q1 2026: Spark `prime_agent_revenue` gains $891K + $891K + $805K = +$2.59M from the three Q1 interest sweeps; the **monthly-settlement bias on Anchorage closes** (Sky was already charging BR on the $150M via the standard ilk-debt mechanic — `Vat.ilks(ALLOCATOR-SPARK-A).Art` includes every USDS Spark has drawn, and Anchorage isn't PSM-netted or Sky-Direct-reimbursed, so it flowed into `utilized × BR` cleanly the whole time). The remaining work under issue #17 is balance-sheet / methodology rather than a numbers gap: snapshot-module position value (today $0 because the escrow holds ~$0 USDC), accrual-vs-cash methodology agreement with Spark, and an automated principal/interest split at loan termination. PR's a partial close — closes the bias, leaves the refinements open.

**Acknowledged trade-offs (no fix):**
- **`Venue.sky_direct: bool`** — kept on the dataclass as a no-op for legacy YAML compatibility but ignored by compute (SDE classification driven entirely by `config/sky_direct_exposures.yaml`). Will be removed once all `{prime}.yaml` files have migrated to omit the field.
- **`VenueRevenue.br_charge` and `sky_direct_shortfall`** — always emit `0` under the SDE-split model. Kept in `VenueRevenue` and the Load layer (`provenance.json`, `pnl.csv`) for round-trip compatibility with settlements written under the older floor model. New runs report the same data via `sd_share` and `sd_revenue`. The Load layer should add `sd_share` / `sd_revenue` columns alongside the legacy fields.
- **`sd_share_d = 0` when `v_d = 0`** (capped SDE venue opening mid-month) — days with zero position value contribute $0 to `sd_revenue`. If a venue is opened mid-month and the daily timeseries starts at 0, Sky's claim starts accumulating from the first day of non-zero balance.
- **`kind=pattern` SDE entries (PSM3 USDC non-Eth)** — present in `config/sky_direct_exposures.yaml` but `compute_sky_revenue` does not yet honor them; loud `_log.warning` emitted at config load. Resolved when the PSM3-as-SDE accounting layer lands. **No live impact for Spark today** because Spark's PSM3 USDC holdings are already netted out of `utilized` via `psm_usds`, so the prime is reimbursed BR; the only difference is the missing direct revenue claim for Sky.
- **PSM chains not in `prime.alm`/`subproxy`** — `_resolve_pin_blocks` only resolves chains in `prime.chains` (= alm ∪ subproxy keys). If a future prime adds a PSM-only chain, the PSM contribution would silently skip and inflate `sky_revenue`. **No live impact** — every current prime has the PSM chain in `prime.alm` (Spark + Grove). Add a coverage assertion in `_aggregate_psm_usds` if this topology becomes possible.
- **`tests/fixtures/grove_fixture_loader.py` hits live RPC** for position-balance / convert-to-assets / NAV oracles when `Sources` overrides are not provided. Acknowledged: acceptance scripts are designed to run with live RPC env vars set (`ETH_RPC` / `BASE_RPC` / etc.); pure unit tests inject mocks. Future: add `pytest.mark.integration` to acceptance-style tests so CI without env vars cleanly skips.
- **Dune query-result cache invalidation by SQL content** — `@cached(source_id="dune.execute")` keys on (`sql_path`, `params`, `pin_block`). Editing a SQL file creates a new Dune query ID via `dune_ids.json` but the cached pickle for the old SQL keyed on the same path is still served. Mitigation today: `SETTLE_NO_CACHE=1` after SQL changes. Document loudly; future fix is to fold `sha256(sql_text)` into the cache key.
- **`parents[3]` config path resolution** in `domain/sde.py`, `domain/subsidy.py`, `domain/config.py` — assumes the package is a clone of the repo, not a wheel install. Acceptable for this project; flag if MSC is ever packaged.

#### Code-review acks (2026-06-09 — Spark SubProxy + Grove E23 boundary-miss fix)

PR addressing two related Dune `tokens.transfers` indexing gaps that produced material settlement errors. Three independent code-reviewer agents ran adversarially against the diff; their must-fix findings (1, 2, 3 below) were addressed in the same PR.

**Fixed:**
- **Spark `agent_rate = $0` root cause (config/spark.yaml)** — `config/spark.yaml` had `0x691a6c29e9e96dd897718305427ad5d534db16ba` listed as Spark's subproxy. That address is the **urn** (the borrower position in the Vat for ALLOCATOR-SPARK-A), not the SubProxy contract. The actual SubProxy at `0x3300f198988e4C9C63F75dF86De36421f06af8c4` holds ~$30–37M idle USDS earning the SSR+agent_rate spread; the urn holds $0. With the urn-as-subproxy misconfiguration `agent_rate` correctly read $0 for every month (the urn really does hold $0); fixed to ~$96K–$124K/month across Jan–May 2026, matching ~$32–37M USDS × SSR+20bps. **PRD §17.12 identifier table updated to distinguish urn vs SubProxy.**
- **Grove E23 (Steakhouse Prime Instant, Base) — Dune indexing gap at the pin block (positions.py)** — Dune `tokens.transfers` missed a 2,919,004 steakUSDC mint to the Grove Base ALM at the exact May 31 23:59:59 UTC pin block (block 46,741,326). The events-reconstructed cum_inflow was $3.27M short of the on-chain reality, surfacing as $3.27M of phantom revenue in May (a 51% APY vs the real ~5% APY). `_shares_to_usd_inflow_timeseries` now optionally compares period-only events Δshares against on-chain `balanceOf(eom_block) − balanceOf(som_block)` and attributes the discrepancy as a synthetic inflow row at `period.end`, priced at `price_at_block(pin_block)` (the same block used to read on-chain Δshares — dodges any drift between the block resolver's EoD definition and the orchestrator's pin). Skips venues with `share_burn_destinations` (Maple-style withdrawal queues where the events-vs-balanceOf invariant correctly does not hold). Grove May 2026 revenue: $3,269,077 → $269,333.
- **Subproxy pre-period funding anchor (balances.py)** — `get_subproxy_balance_timeseries` now optionally compares Dune-tracked SoM cum_balance against on-chain `balanceOf(som_block)` and prepends a synthetic seed row at `prime.start_date` for the gap. This is the mechanism that resurrects Spark's $30–37M of pre-period Sky-allocated USDS into the agent_rate base. A second EoM cross-check warning fires if the tracked EoM (post-seed-shift) still diverges from on-chain EoM — surfaces mid-period out-of-band transfers Dune missed (the SoM anchor alone wouldn't catch those).
- **Production registry fallback for `position_balance`** — both new call sites (`monthly_pnl.py` subproxy + Cat B inflow dispatch) initially read `sources.position_balance.balance_at` with no registry fallback. The default `Sources()` carries `position_balance=None`, so a production run that didn't explicitly inject a source would have silently disabled the new anchor (or crashed at the Monad Cat B closed-form path). Now falls back to `get_position_balance_source()` from the registry, matching the pattern used for `sources.balance` elsewhere in the file. Caught by code-reviewer agent 2.

**Acknowledged trade-offs (no fix):**
- **Synthetic-row pricing for hypothetical mid-period misses.** The pin-block-pricing fix above is exact for pin-block-boundary misses (the Grove E23 scenario), but if a future `_shares_to_usd_inflow_timeseries` reconciliation fires for a missed event whose true economic date was mid-period, the synthetic row at `period.end` priced at `pps(pin_block)` will be mispriced by the (EoM − event-date) pps drift. The warning log captures the discrepancy magnitude so reviewers can spot-check; the docstring documents the limitation explicitly.
- **OBEX `test_against_oracle_replay` failure pre-exists on main** — the e2e test fails with `sky_revenue 2060412 vs 1981127` on both `main` and this branch with identical values; the divergence is from oracle-SQL methodology, not from this PR. Not in scope.
- **Spark SubProxy mid-period USDS flows initially masked by the fixture loader — FIXED 2026-06-09 same PR.** The new EoM cross-check fired on Spark Apr 2026 (+$525,726) and May 2026 (+$123,680). Investigation: Dune `tokens.transfers` HAS all 23 relevant Transfer rows for 2026 (verified via direct query — sum matches on-chain delta to the cent), so NOT a Dune indexing gap. The culprit was `tests/fixtures/spark_fixture_loader.py:313` returning `_empty_balance_df()` for any un-routed `(token, holder)` pair, with a comment stating "Spark Eth subproxy/ALM raw USDS+sUSDS confirmed ~$0 (dust)" — written when `config/spark.yaml` listed the urn (`0x691a…`, legitimately $0) as the subproxy. With the corrected SubProxy address (`0x3300…f8c4`, holds $30–37M USDS with monthly flows), the empty-df fallback was silently underpaying `agent_rate`. Initial impact estimate was "~$300/month max" (Apr + May only); a more careful per-day analysis caught **Feb 2026 carrying a $5.97M average mid-period balance gap → $18,877 underpayment in Feb alone**, with cumulative Jan–May underpayment of **+$18,568** (≈ 3.3% of Spark's $563,872 pre-fix headline; post-fix headline = $582,440). **Resolution:** captured `subproxy_usds_timeseries.json` + `subproxy_susds_timeseries.json` via the canonical `transfer_timeseries.sql` Dune query (id 7432800), wired `spark_fixture_loader._RoutedBalances.cumulative_balance_timeseries` to route `(USDS, SubProxy)` and `(sUSDS, SubProxy)` queries to those fixtures, added `refresh_subproxy_timeseries()` to `scripts/extend_spark_fixtures.py` for future refresh. Post-fix: both the SoM-anchor and EoM-cross-check warnings stay silent for Spark (fixture and on-chain agree), and the missing agent_rate is now captured. Per-month delta: Jan $0 / Feb +$18,877 / Mar -$794 / Apr +$224 / May +$262.

#### Snapshot module (2026-05-04) — point-in-time balance sheet vs BA labs

The standalone `data/recompute/` work folded back into the production `settle/` package as a new `src/settle/snapshot/` module. Distinct from monthly settlement (which computes period revenue), a snapshot is the prime's balance sheet at one block — same shape as BA labs' `stars-api.blockanalitica.com`.

```bash
python -m settle snapshot --prime grove
python -m settle snapshot --prime spark [--block N] [--json]
```

**What's reused** (no new pricing math): the snapshot calls `normalize.positions.get_position_value()` for every venue — same code path as the monthly settlement. V3 NFT pricing, Curve LP, RWA NAV oracles, ERC-4626 vaults all share the production primitives. The new code in `src/settle/snapshot/` is just (a) per-chain "now" block resolution, (b) idle/treasury aggregation at the subproxy, (c) the Vat.ilks() debt read, (d) BA-shaped output assembly.

**Validated against BA labs (live)**:
- **Grove debt** = $3,181,207,993.75 (snapshot, via `Vat.ilks(BLOOM-A)`) === BA `debt` field. Bit-exact match.
- **Spark debt** = $4,299,055,290.13 (snapshot) === BA `debt`. Bit-exact.
- **Treasury** (subproxy USDS via `balanceOf`) = $22,818,516.00 (Grove) === BA `treasury_balance`. Bit-exact.
- **Per-venue pricing**: at every address BA also indexes (16 venues across both primes), our value drift vs BA is **<0.26%** — well under the 0.5% test tolerance. Categories validated: A (par-stable), B (4626), C (aToken), E (RWA — Chronicle + pricePerShareFeed), F (Curve LP + V3 NFT). Includes E12 Uniswap V3 NFT pricing ($25M) which the existing `_uniswap_v3_value` already supports.

**Headline aggregates that intentionally differ from BA** (BA's decomposition uses opaque protocol-level rules we can't fully reverse-engineer):
- **`assets`**: BA's `/stars/{prime}/ assets` is bigger than our position-sum (Grove +$325M, Spark +$1B). BA aggregates at the Sky-protocol level (likely `urn.ink × spot` or a Sky-internal "total deployed" metric), not by summing on-chain positions. Our `assets` is the verifiable on-chain sum.
- **`idle_assets`** + **`treasury_balance`**: BA's classification (Spark $720M idle + $37M treasury) is opaque — addresses they include aren't documented. Ours reports subproxy-only (avoids double-counting venues that already track ALM-side holdings).
- **`liabilities`**: BA's `liabilities` for Spark = `debt + sUSDS_POL` ($6.77B = $4.30B + $2.47B). Ours = debt only. Open question: is BA's `liabilities` a Sky-savings-system accounting choice or specific to Spark's sUSDS exposure?
- **`nav`**: derived from above, drifts accordingly. Ours reports the on-chain truth (Grove ~−$318M deficit on positions vs debt, Spark ~$1.46B surplus); BA shows both at near-zero, consistent with their inflated `assets`/`liabilities`.

**Parity test**: `tests/integration/test_ba_parity.py` runs live (gated by `@pytest.mark.live`):
- Hard-asserts: debt within $100, Grove treasury within $100, per-venue values within 0.5% drift on every BA-indexed venue.
- Soft-reports (printed): full top-line side-by-side, per-venue table with absolute + % diff. RPC-erroring venues (drpc free-tier rate limits on L2s) skipped, not failed.

Run with: `pytest tests/integration/test_ba_parity.py -m live -v -s`.

**Open questions** — full text in `QUESTIONS.md` (BA labs section). Summary: (1) what addresses make up BA's Spark `idle_assets` $720M + `treasury` $37M (**B1**), (2) is BA's `liabilities = debt + sUSDS_POL` intentional (**B3**), (3) how does BA derive `assets` (which exceeds our position-sum by ~$325M Grove / ~$1B Spark — **B4**, currently the only P0 in the BA section), (4) which NAV oracle is canonical for STAC (E7 drift ~1.7% — Chronicle vs const_one, with Redstone independently confirming Chronicle within 4 bps — **B5**).

**Operational known-divergences** (reported by parity test, not failed):
- **E7 STAC ~1.7% drift** — Chronicle vs const_one (whitelisted in `KNOWN_NAV_DIVERGENCES = {"E7"}`); Redstone (`0xedc6…3add4`) cross-checked 2026-05-12 and agrees with Chronicle within 4 bps, so the drift is genuine NAV growth and BA's const_one is the outlier.
- **S37 / S47 sUSDS proxies on L2** — drpc free-tier rate-limits the `balanceOf` reads; snapshot returns $0 with a WARNING log; test treats as `SKIP`. Resolves with paid drpc/Alchemy or an Alchemy fallback in the `extract.rpc` retry chain.

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
