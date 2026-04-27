# MSC Settlement — hybrid Dune / RPC / off-chain architecture

How to mix Dune SQL, blockchain RPC, and off-chain APIs into one auditable monthly settlement pipeline per prime agent.

---

## 1. The problem

The MSC monthly PnL formula is:

```
monthly_pnl = prime_agent_revenue + agent_rate − sky_revenue
```

Each component has very different data shape:

| Component | Nature | Best-fit source | Why |
|---|---|---|---|
| `sky_revenue` | Cumulative interest on utilized USDS over the month | **Dune** | Pure event aggregation — `frob` calls + USDS transfers + SSR boundaries. Small, readable SQL. |
| `agent_rate` | Cumulative APY on subproxy USDS+sUSDS holdings | **Dune** | Same shape — transfers + rate windows. Small SQL. |
| `prime_agent_revenue` | Δ(position_value − cost_basis) over the month | **Mixed** | Cost basis is event-aggregated (Dune-natural). Position value is per-venue NAV math (Python-natural). |

The pain point we hit empirically (POC, [VALUATION_METHODOLOGY.md](VALUATION_METHODOLOGY.md)): translating "balance × `convertToAssets`" or "scaled balance × `liquidityIndex`" into one DuneSQL block produces ~50-line CTEs with `CAST(... AS DOUBLE)` everywhere, mixed nominal/scaled semantics, and a partition-pruning trap on `prices.minute`. Reviewable by the author who wrote it; opaque to the next reviewer.

The same calculation in Python is ~5 lines: `balanceOf(alm) × convertToAssets(1) / 1e18 × underlying_price`.

**Goal:** route each piece of work to the source where it's clearest, while keeping the whole pipeline reproducible, swappable, and auditable.

---

## 2. Core principles

These come from data-engineering practice (medallion / lakehouse) adapted to our scale (small team, one settlement per month per prime, ~30 venues max).

1. **One source of truth per datum.** Cumulative debt is computed in exactly one place (Dune). Position NAV is computed in exactly one place (Python). No parallel implementations to drift apart.

2. **Layered, with crisp contracts between layers.** Bronze (extract) → Silver (canonical Python objects) → Gold (business metrics). Downstream layers never reach into upstream sources directly.

3. **Sources are pluggable behind protocols.** Today's `DuneDebtSource` should be replaceable by tomorrow's `SubgraphDebtSource` without touching Gold-layer code.

4. **Pin to a block.** Every settlement run pins to a single `block_number` per chain. Dune queries use `block_number <= :pin`, RPC calls use that block as `block` parameter, API calls record their fetch timestamp. No "latest"-mode anywhere in production.

5. **Cache extracts, recompute everything else.** Bronze extracts are expensive (Dune credits, RPC quotas) — cache them keyed by `(source, query, block)`. Silver and Gold are cheap pure functions — re-derive every run.

6. **Outputs are diff-able.** A settlement run produces a Markdown + CSV that lives under `agents/<prime>/settlements/<month>/`. The PR-review unit is a settlement file, not a Dune dashboard URL.

7. **Reviewers see Python, not SQL.** Even when the underlying data comes from Dune, the calculation steps a reviewer reads are Python expressions. SQL is constrained to "select these aggregated events from this table" — i.e. data plumbing only, no business logic.

---

## 3. Three layers

```
                           ┌─────────────────────────────────────────────┐
                           │  Sources (external)                          │
                           │  ─────────────────                           │
                           │  • Dune Spellbook (event-indexed warehouse) │
                           │  • Alchemy / public RPC per chain            │
                           │  • Issuer APIs (Centrifuge, Superstate, …)  │
                           │  • CoinGecko / market price feeds            │
                           └─────────────────────┬───────────────────────┘
                                                 │
                           ┌─────────────────────▼───────────────────────┐
   BRONZE                  │  Source-specific extractors                  │
   raw extracts            │  ───────────────────────────                 │
                           │  dune_client.execute(query_id, block_pin)    │
                           │  rpc.eth_call(contract, fn, block)           │
                           │  requests.get(api_endpoint)                  │
                           │                                              │
                           │  Cached on disk by (source, args, block).    │
                           │  Each returns a typed Python object.         │
                           └─────────────────────┬───────────────────────┘
                                                 │
                           ┌─────────────────────▼───────────────────────┐
   SILVER                  │  Domain primitives (canonical Python)       │
   per-concept getters     │  ────────────────────────────────────       │
                           │  get_debt_timeseries(prime, period)          │
                           │  get_subproxy_balances(prime, period)        │
                           │  get_ssr_history(period)                     │
                           │  get_position_balance(prime, venue, block)   │
                           │  get_position_price(venue, block)            │
                           │  get_position_value(prime, venue, block)     │
                           │                                              │
                           │  Each function picks its source internally.  │
                           │  Returns DataFrame or Decimal with units.    │
                           └─────────────────────┬───────────────────────┘
                                                 │
                           ┌─────────────────────▼───────────────────────┐
   GOLD                    │  Business metrics                            │
   pure-Python compute     │  ─────────────────                           │
                           │  compute_sky_revenue(prime, month)           │
                           │  compute_agent_rate(prime, month)            │
                           │  compute_prime_agent_revenue(prime, month)   │
                           │  compute_monthly_pnl(prime, month)           │
                           │                                              │
                           │  No I/O. Pure math on Silver outputs.        │
                           │  Trivially testable with fixtures.           │
                           └─────────────────────┬───────────────────────┘
                                                 │
                           ┌─────────────────────▼───────────────────────┐
   ARTIFACT                │  agents/<prime>/settlements/2026-04.md       │
                           │  + 2026-04.csv (machine-readable)            │
                           │  Committed to git. PR review = diff review.  │
                           └─────────────────────────────────────────────┘
```

### 3.1 Bronze — source-specific extractors

One Python module per source. Their job is "fetch raw bytes, return typed object". No business logic.

```python
# bronze/dune.py
def execute_query(query_id: int, params: dict, pin_block: int) -> pd.DataFrame: ...

# bronze/rpc.py
def eth_call(rpc_url: str, contract: str, selector: str, args: bytes, block: int) -> bytes: ...
def get_balance(rpc_url: str, address: str, block: int) -> int: ...

# bronze/external.py
def coingecko_price(coin_id: str, ts: datetime) -> Decimal: ...
def centrifuge_nav(token: str, date: date) -> Decimal: ...
```

All Bronze extractors are wrapped by a common cache:

```python
@cache_on_disk(key=lambda *a, **k: hash_args(a, k))
def execute_query(...): ...
```

Cache key includes the pin block / pin timestamp, so two settlement runs at the same block hit the same data. Cache invalidates on `(source, args, block)` triple.

### 3.2 Silver — domain primitives

These are the **only** functions Gold-layer code calls. Each has a single signature and chooses its source internally.

```python
# silver/debt.py
def get_debt_timeseries(prime: Prime, period: Period) -> pd.DataFrame:
    """Daily cum_debt (USDS) for a prime over a period.
    Source: Dune `frobs` query. Returns columns [block_date, cum_debt]."""
    return _dune_debt_query(prime.ilk_bytes32, period.start, period.end, pin=period.pin_block)

# silver/balances.py
def get_subproxy_balance_timeseries(prime: Prime, token: Token, period: Period) -> pd.DataFrame:
    """Daily cum balance of `token` in subproxy. Source: Dune transfers."""

def get_position_balance(prime: Prime, venue: Venue, block: int) -> Decimal:
    """Current balance of `venue.token` held by the ALM.
    Source: RPC eth_call balanceOf — returns rebased amount for aTokens."""

# silver/prices.py
def get_unit_price(venue: Venue, block: int) -> Decimal:
    """Price per unit of venue.token at `block`.
    Source dispatch: $1 for stables, convertToAssets for 4626, NAV for RWA, oracle for gas."""

# silver/ssr.py
def get_ssr_history(period: Period) -> pd.DataFrame: ...
```

The contract is the function signature, not the underlying SQL or RPC call. Tomorrow's subgraph indexer plugs in via the same signature.

### 3.3 Gold — business logic

Pure Python on Silver outputs:

```python
# gold/sky_revenue.py
def compute_sky_revenue(prime: Prime, month: Month) -> Decimal:
    debt    = silver.debt.get_debt_timeseries(prime, month.period)
    subproxy = silver.balances.get_subproxy_balance_timeseries(prime, USDS, month.period)
    alm_usds = silver.balances.get_alm_balance_timeseries(prime, USDS, month.period)
    susds    = silver.balances.get_subproxy_balance_timeseries(prime, sUSDS, month.period)
    ssr      = silver.ssr.get_ssr_history(month.period)

    daily = debt.join(subproxy).join(alm_usds).join(susds).join(ssr, on='block_date')
    daily['utilized']   = daily.cum_debt - daily.cum_sub_usds - daily.cum_alm_usds - daily.cum_susds
    daily['borrow_apy'] = daily.ssr + Decimal('0.003')
    daily['daily_rev']  = daily.utilized * ((1 + daily.borrow_apy) ** (1/365) - 1)
    return daily.daily_rev.sum()
```

A reviewer reads ~10 lines of Python and follows the math. Behind each `silver.*` call is the right source, but the reviewer doesn't need to debug DuneSQL CTEs to check the formula.

---

## 4. Routing — what runs where

This table is the contract for which Silver function uses which source.

| Silver function | Primary source | Why |
|---|---|---|
| `get_debt_timeseries` | **Dune** (`ethereum.traces` for `frob` calls) | Event aggregation over months — Dune's strength |
| `get_subproxy_balance_timeseries` | **Dune** (`tokens.transfers`) | Same |
| `get_alm_balance_timeseries` (USDS only) | **Dune** | Same |
| `get_ssr_history` | **Dune** (`file()` traces on sUSDS) | Same |
| `get_venue_inflow_timeseries` (cost basis) | **Dune** (`tokens.transfers`) | Same |
| `get_position_balance` (rebasing aToken/spToken) | **RPC `balanceOf`** | One call vs ~50-line scaled-events CTE; POC validated 0.001% match |
| `get_position_balance` (ERC-4626 vault) | **RPC `balanceOf`** | Same |
| `get_position_balance` (par stable in ALM) | **Dune** (`tokens.transfers`) | Trivial cumulative sum |
| `get_unit_price` (par stables) | **Hardcoded $1** | — |
| `get_unit_price` (4626 vault share) | **RPC `convertToAssets`** | Canonical |
| `get_unit_price` (aToken / spToken) | **Hardcoded $1 (underlying peg) or RPC oracle** | — |
| `get_unit_price` (RWA — Centrifuge) | **Issuer API** then CoinGecko fallback | Authority |
| `get_unit_price` (RWA — BlackRock / Superstate) | **Issuer API** | Authority — no on-chain getter |
| `get_unit_price` (Curve LP) | **RPC**: `pool.balances(i)` + `convertToAssets` for sUSDS | POC Method B |
| `get_unit_price` (governance, MORPHO/etc.) | **CoinGecko or Dune `prices.minute`** | One canonical choice — see Q10 in QUESTIONS |
| `get_unit_price` (native gas) | **CoinGecko** | Outside `tokens.transfers` |

A useful mental shortcut:

> **Cumulative-over-time work → Dune. Snapshot-state work → RPC. Off-chain authority → API.**

---

## 5. Source abstraction — making the indexer pluggable

The user noted we may move off Dune for some workloads. Best-practice answer: protocols at the Silver boundary.

```python
# silver/protocols.py
class IDebtSource(Protocol):
    def debt_timeseries(self, ilk: bytes32, start: date, end: date, pin: int) -> pd.DataFrame: ...

class IBalanceSource(Protocol):
    def balance_at(self, token: str, holder: str, block: int) -> int: ...

class IPriceSource(Protocol):
    def price_at(self, token: str, block: int) -> Decimal: ...

# silver/debt.py
def get_debt_timeseries(prime, period, source: IDebtSource = default_dune_debt_source):
    return source.debt_timeseries(prime.ilk_bytes32, period.start, period.end, period.pin_block)
```

Implementations:

```python
# bronze/sources/dune_debt.py
class DuneDebtSource:
    def debt_timeseries(self, ilk, start, end, pin):
        return execute_query(SHARED_FROBS_QUERY_ID, {'ilk': ilk, 'start': start, 'end': end}, pin)

# bronze/sources/subgraph_debt.py
class SubgraphDebtSource:
    def debt_timeseries(self, ilk, start, end, pin):
        return self.client.query(GRAPHQL_FROBS, ilk=ilk, start=start, end=end, block=pin)
```

Switching is a config change:

```yaml
# config/grove.yaml
sources:
  debt: dune          # or: subgraph
  balances: dune      # or: rpc-archive
  prices: rpc+coingecko
```

Tests pin a `MockDebtSource` and assert Gold-layer computations regardless of where the data came from.

---

## 6. Operational concerns

### 6.1 Reproducibility

Every settlement run is parameterized by `(prime, month, pin_block_per_chain)`. Re-running with the same triple produces byte-identical outputs.

- Pin block recorded in the artifact's frontmatter.
- Cache is content-addressed by the pin.
- External APIs that don't accept a block (CoinGecko) record their fetch timestamp; if the result drifts on re-run, the cache hit prevents recomputation.

### 6.2 Validation gates

| Layer | Validation |
|---|---|
| Bronze | Source returned non-empty result; row count matches expected window |
| Silver | Schema check (pandera / pydantic); units are correct (USD, not raw); decimals consistent |
| Gold | Sanity invariants — `monthly_pnl ≈ unrealized_gain_eom − unrealized_gain_som + agent_rate − sky_revenue` (round-trip check); `cum_debt ≥ 0`; `agent_demand ≤ cum_debt`; `Σ cost_basis ≈ cum_debt − cross_chain_out` (the [grove/QUESTIONS.md](grove/QUESTIONS.md) Q6 reconciliation) |

Validation failures **don't** silently emit a settlement — they raise.

### 6.3 Observability

Each Silver call logs `(function, source, block, runtime_ms, n_rows, sha256_of_result)`. The settlement artifact includes this log so a future reviewer can see exactly what was fetched.

### 6.4 Snapshot drift between Dune and RPC

POC measured 0.0007% drift on aHorRwaRLUSD between Dune and Python at the same `block_number` cutoff — purely from the live block clock running on Python. **Mitigation:** Silver pins all RPC calls to the same block as Dune. Drift becomes 0.

### 6.5 Source disagreement detection

For tokens where two sources cover the same datum (Dune `prices.minute` vs CoinGecko for MORPHO; CoinGecko vs Centrifuge API for JTRSY), Silver always picks ONE per [QUESTIONS Q10](valuation_poc/QUESTIONS.md#q10-price-source-drift-between-pricesminute-and-coingecko), but logs the other for reconciliation. If they disagree by > X bps, raise.

---

## 7. Practical layout

Suggested module tree (extending the current `agents/shared/`):

```
msc/
├── settle/                            # the new package
│   ├── pyproject.toml
│   ├── src/settle/
│   │   ├── bronze/
│   │   │   ├── dune.py                # Dune SDK wrapper + cache
│   │   │   ├── rpc.py                 # JSON-RPC wrapper + cache
│   │   │   ├── coingecko.py
│   │   │   └── issuer/
│   │   │       ├── centrifuge.py
│   │   │       ├── superstate.py
│   │   │       └── blackrock.py
│   │   ├── silver/
│   │   │   ├── protocols.py           # IDebtSource, IBalanceSource, IPriceSource
│   │   │   ├── debt.py
│   │   │   ├── balances.py
│   │   │   ├── prices.py
│   │   │   └── ssr.py
│   │   ├── gold/
│   │   │   ├── sky_revenue.py
│   │   │   ├── agent_rate.py
│   │   │   ├── prime_agent_revenue.py
│   │   │   └── monthly_pnl.py
│   │   ├── domain/
│   │   │   ├── primes.py              # Prime, Venue, Token dataclasses
│   │   │   └── period.py
│   │   ├── validation.py              # Pandera schemas, invariant checks
│   │   └── cli.py                     # `python -m settle run --prime grove --month 2026-04`
│   └── tests/
│       ├── fixtures/                  # frozen Bronze outputs for replay tests
│       └── test_gold_*.py             # pin a fixture, assert PnL number
├── agents/
│   ├── grove/
│   │   ├── settlements/
│   │   │   ├── 2026-03/
│   │   │   │   ├── pnl.md             # Generated artifact
│   │   │   │   └── pnl.csv
│   │   │   └── 2026-04/...
│   │   └── ...
│   └── shared/
│       ├── ASSET_CATALOG.md
│       ├── VALUATION_METHODOLOGY.md
│       ├── SETTLEMENT_ARCHITECTURE.md  ← this file
│       └── valuation_poc/...
└── queries/                           # Dune SQL files, version-controlled
    ├── shared/
    │   ├── debt_timeseries.sql
    │   ├── transfer_timeseries.sql
    │   └── ssr_history.sql
    └── per-prime/
        └── grove_venue_inflows.sql
```

Key conventions:

- **Dune SQL lives in `queries/`** as plain SQL files. The Bronze extractor reads them, substitutes parameters, executes. Version-controlled and PR-reviewable just like Python.
- **Settlement artifacts live under `agents/<prime>/settlements/<month>/`** — git history of monthly PnL is the audit log.
- **No notebooks in production** — the CLI is the only entry point. Notebooks belong in scratch dirs not committed to the repo.

---

## 8. The Gold computation, end to end

Concrete sketch of `monthly_pnl` for a single prime:

```python
def compute_monthly_pnl(prime: Prime, month: Month) -> MonthlyPnL:
    period   = Period.from_month(month, pin_block_eth=resolve_eod_block('ethereum', month.end))

    debt     = silver.debt.get_debt_timeseries(prime, period)
    subproxy = silver.balances.subproxy_balances(prime, period)
    alm_usds = silver.balances.alm_token(prime, USDS, period)
    ssr      = silver.ssr.history(period)

    sky_rev   = gold.sky_revenue.compute(debt, subproxy, alm_usds, ssr)
    agent_rev = gold.agent_rate.compute(subproxy, ssr)

    venues_som = [
        silver.positions.value_at(prime, v, period.start_block)
        for v in prime.venues
    ]
    venues_eom = [
        silver.positions.value_at(prime, v, period.end_block)
        for v in prime.venues
    ]
    cost_basis_delta = silver.cost_basis.delta(prime, period)
    prime_rev = sum(venues_eom) - sum(venues_som) - cost_basis_delta

    monthly_pnl = prime_rev + agent_rev - sky_rev

    validation.check_invariants(monthly_pnl, debt, subproxy, ...)

    return MonthlyPnL(
        prime=prime, month=month,
        sky_revenue=sky_rev, agent_rate=agent_rev,
        prime_agent_revenue=prime_rev, monthly_pnl=monthly_pnl,
        provenance=collect_log(),
    )
```

A reviewer sees: which Silver calls feed which Gold formula, plus the math. They don't need to read 200 lines of CTEs to check that the borrow rate is `SSR + 30bps`.

---

## 9. Migration from current state

We have today: shared parameterized Dune queries, per-prime monthly_pnl Dune queries (OBEX done, Grove planned), the Python POC. Migration is incremental:

| Phase | Work | Outcome |
|---|---|---|
| 1 | Extract the **debt + agent_rate** logic from `obex_monthly_pnl.sql` into focused queries (`shared/debt_timeseries.sql`, `shared/transfer_timeseries.sql`) and `Bronze/Silver` Python wrappers | `compute_sky_revenue` + `compute_agent_rate` produce the same numbers as the current Dune query |
| 2 | Implement `Silver.positions` for OBEX (single venue, syrupUSDC) using RPC. Replace the Dune `cum_venue × price` part of `obex_monthly_pnl.sql`. | Cleaner codebase; OBEX settlement matches forum posts modulo the documented APR/APY discrepancy |
| 3 | Onboard Grove (~18 venues). Each new venue is a Python `Venue` config; no new Dune queries needed beyond `transfer_timeseries.sql`. | Grove monthly PnL produced via Python compose with Dune debt + RPC NAV |
| 4 | Add second source (subgraph or self-hosted indexer) by implementing `SubgraphDebtSource`. Run side-by-side with Dune for one month, compare; cut over | Indexer-portable settlement |

Phases 1 and 2 don't break anything — the existing OBEX Dune query stays as a reconciliation oracle while the new pipeline ramps up. Cut-over only when Phase 1+2 produce identical numbers for at least one month.

---

## 10. Anti-patterns to avoid

1. **Putting business logic in SQL.** "Compute the daily borrow rate" is a Python expression, not a `CASE WHEN block_date < '2025-11-07' THEN 1.048` chain. SQL emits raw aggregates; Python composes them.
2. **Mixing Bronze and Silver concerns.** A function that calls `dune.execute(...)` AND computes a 4626 conversion AND multiplies by USD price is doing three jobs. Split it.
3. **"Just pull `latest` from the RPC".** Always pin to the period's end block. "Latest" is a recipe for non-reproducibility.
4. **Silent fallbacks.** If the issuer API returns 503 and we fall back to CoinGecko, log it loudly and fail the settlement run if it happens during a non-test month.
5. **Per-venue Dune queries.** One generic `transfer_timeseries.sql` parameterized by token + holder; thousands of dashboards have already proven this scales. Don't write `grove_jaaa_inflows.sql`, `grove_jtrsy_inflows.sql`, …
6. **Floating-point in the Gold layer.** Use `Decimal` end-to-end for USD amounts. APY math is the only place where `(1 + r) ** (1/365)` warrants `float`, and even there, cast back before assembling totals.
7. **Settlement-time API calls.** All external calls happen in Bronze, are cached, and are explicitly invokable from a `prefetch` subcommand. The settlement compute itself never reaches across the network — making it deterministic and fast.

---

## 11. Open design questions

These connect back to [valuation_poc/QUESTIONS.md](valuation_poc/QUESTIONS.md):

- **Cache backend** — disk-based JSON / Parquet, DuckDB, or a small SQLite? Picking DuckDB gives free SQL over cached frames at the cost of a dependency.
- **Settlement orchestration** — Make / Just / a tiny CLI is enough at our scale; revisit if we run > 1 settlement per day across > 5 primes.
- **Schema versioning** — do we version the Silver-layer schemas to avoid silent breakage when a Dune spell updates? Recommend yes, via Pandera schema annotations.
- **Indexer alternative spike** — pick one Silver function (debt timeseries is the cleanest) and prototype `SubgraphDebtSource` against The Graph or a self-hosted Goldsky/Subsquid feed; benchmark vs Dune for cost + latency before broader migration.
