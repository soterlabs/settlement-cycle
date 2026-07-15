# Migrating Dune queries to Envio HyperSync — a playbook

Written for the next person (or agent) moving a settlement data source off Dune.
It encodes what actually worked, the traps that cost time, and the discipline
that kept counterparty-facing numbers correct. Read it before writing code.

> **The one-line lesson:** use **HyperSync-direct** (the raw query API), not
> HyperIndex. Envio serves **logs / blocks / transactions / traces** — never
> `eth_call`. Match the incumbent **to the wei**, gate every change on
> byte-identical `summary.md`, and never let a source silently fall back to a
> wrong number.

---

## 0. Decide: can this query even move?

Classify the Dune query by what it reads (grep its `FROM`):

| Dune reads… | Example | Move to | Notes |
|---|---|---|---|
| Standard event logs | `tokens.transfers`, `*_evt_Deposit`, `ethereum.logs` | **HyperSync-direct** (logs) | The easy case. |
| Anonymous events / call traces | `ethereum.traces` (Vat `frob`/`grab`, sUSDS `file()`) | **HyperSync-direct** (raw `topic0` / traces) | **HyperIndex CANNOT** — anonymous 4-indexed events, [enviodev/hyperindex#990](https://github.com/enviodev/hyperindex/issues/990). |
| Contract **state** (`eth_call`) | `balanceOf` of rebasing aToken, `convertToAssets`, AMM `slot0`/`virtual_price` | **Reconstruct from events if possible, else stay RPC** | No Envio product does `eth_call` (HyperRPC is logs/blocks/txns only). Rebasing aTokens *are* reconstructable (§5); 4626/AMM state generally is **not**. |
| A Dune-hosted **derived** table | `dune.sparkdotfi.result_savings_v2_*` | **Cannot move** | Not raw chain data — someone else's computed dataset. Keep on Dune or re-derive the methodology from scratch. |
| Block-by-timestamp | `evms.blocks` | **HyperSync** (`block_timestamp` + binary search) | See §4; big win (largest RPC bucket). |

If it's "cannot move," stop and document why. If it's `eth_call` state with no
event, it stays RPC — that's the irreducible floor, and it's fine.

## 1. Architecture you plug into

- **Protocols** (`normalize/protocols.py`): `IDebtSource`, `IBalanceSource`,
  `IBlockResolver`, `IPositionBalanceSource`, `INavOracleSource`, … A source is
  any class matching the protocol.
- **Registry** (`normalize/registry.py`): `_<FAMILY>_SOURCES = {"dune": …,
  "hypersync": …}`. Add your class here.
- **Per-prime switch**: `config/<prime>.yaml` `sources:` block, e.g.
  `sources: {debt: hypersync, balance: hypersync}`. Validated at load
  (`domain/config.py::_validate_sources` — add your family to `allowed`) and
  merged in `compute/monthly_pnl.py::_sources_from_prime`. **This is how you
  migrate one prime at a time** without touching others.
- **HyperSync client** (`extract/hypersync.py`): `query_logs`, `block_timestamp`,
  `find_block_at_or_before`, chain→host map, bearer token.
- **Reorg-safe store** (`extract/hypersync_store.py`): `fetch_logs(chain,
  selections, from_block, to_block)` — persists only finalized blocks
  (≤ head − `HYPERSYNC_REORG_MARGIN`), serves historical pins from Postgres,
  fetches only the incremental tail, live pass-through without `DATABASE_URL`.
  **Always fetch logs through the store, not the client directly.**

## 2. Recipe to migrate one source

1. **Read the Dune SQL** and note its exact output columns + semantics.
2. **Build the source** implementing the protocol; fetch via `hypersync_store.fetch_logs`.
3. **Decode** (§3). Compute `topic0` with `extract._keccak`.
4. **Match Dune's numbers exactly** (§6 precision) — this is where it goes wrong.
5. **Self-verify** if you can't be 100% sure it's non-rebasing / correct (§5 pattern).
6. **Register** in `registry.py`; **allow** the family in `_validate_sources`;
   wire `_sources_from_prime`.
7. **Validate** (§7) — isolation harness *and* end-to-end parity.
8. **Enable** in a prime's `sources:` and regenerate; commit only if `summary.md`
   is byte-identical.

## 3. Decoding events

- `topic0` = `keccak256("Event(types)")` — **except anonymous events**, whose
  `topic0` is the 4-byte function selector, left-aligned. (Maker's `note`
  modifier: `topic0` = `frob` selector `0x76088703…`, `topic1` = ilk.)
- Indexed params are `topic1..topic3`; non-indexed are packed in `data`
  (32-byte words). Addresses in topics are 32-byte left-padded — compare with
  `"0x" + addr.hex().rjust(64,"0")`.
- **Raw log `data` ≠ HyperIndex-decoded param.** A `bytes data` field is
  ABI-encoded (`offset word + length word + payload`), so a value inside it is
  at `payload_offset + 64` in the raw hex. (Vat `dart`: offset 164 in the note
  payload = byte 228 in raw log data.)
- **"holder is from OR to"**: two log selections in one query
  (`topics:[[T0],[holder]]` and `topics:[[T0],[],[holder]]` — empty middle =
  wildcard), then **dedup by `(block, log_index)`** (a self-transfer matches both).
- **Decimal adjustment**: Dune's `tokens.transfers.amount` is *decimal-adjusted*;
  raw log `value` is not — divide by `10**decimals` (get decimals via a cached
  RPC read). Apply Dune's `block_date >= start` filter in Python (HyperSync has
  no partition pruning).

## 4. Block resolution (the biggest, cleanest win)

- `block_timestamp(chain, n)` via `include_all_blocks` (returns the block even
  with no matching logs). Byte-identical to `eth_getBlockByNumber`.
- Binary search mirrors `extract.rpc._find_block_at_or_before_rpc` exactly →
  identical block numbers. Cache the result.
- **Head-edge trap:** `archive_height` may report a block that isn't yet
  query-returnable — probing it raises "block not returned". **Back off** from
  the head to the newest returnable block (already handled in
  `find_block_at_or_before`). Historical pins are unaffected.
- Works on chains whose public RPC is pruned/lagging (e.g. **monad**) — a
  reason to prefer it beyond call-count.

## 5. Rebasing tokens (aTokens) — reconstruct, don't read

`balanceOf` of an Aave/SparkLend aToken is **contract state**, but it's fully
event-derivable (`extract/aave_reconstruct.py`):
- `scaledBalanceOf` ← `Mint`/`Burn` (scaled Δ = `rayDiv(amount, index)`, where
  `amount = value ∓ balanceIncrease`) + `BalanceTransfer` (`value` is already
  scaled).
- `reserveNormalizedIncome` ← reserve's last `ReserveDataUpdated`
  (`liquidityIndex`, `liquidityRate`) linearly accrued to the block:
  `index.rayMul(RAY + rate·Δt / SECONDS_PER_YEAR)`.
- `balanceOf = scaled.rayMul(NI)`. Replicate `WadRayMath` **half-up** rounding
  (`(a·b + RAY/2)/RAY`). Per-token metadata: `POOL()` + `UNDERLYING_ASSET_ADDRESS()`
  (two cached RPC reads).

**Self-verifying hybrid pattern** (used for position balance): probe once per
`(chain, token)` with a non-zero balance — compare reconstruction vs RPC; trust
events only on an exact match, else fall back to RPC. A rebased balance changes
every block, so a one-block probe validates the *decode* (right pool/reserve/
offsets); the math being deterministic makes it exact everywhere. **This is how
you attempt a risky reconstruction without ever shipping a wrong number.**

## 6. Precision — match Dune to the wei

- **Aggregate in exact integers**, divide once at the end under a wide Decimal
  context: `with localcontext() as ctx: ctx.prec = 60`. Never divide per-row
  under the default 28-digit context (loses ~1e-6).
- Beware numpy int64 overflow — use **Python ints** (pandas coerces to int64 and
  silently wraps past 2^63); carry `Decimal`/`int` in object columns.
- **Dune is often *less* precise than you:** Trino `DECIMAL(38,0)/DECIMAL(38,0)`
  truncates to ~6 decimals, so exact-integer HyperSync differs by ~1e-6. That's
  Dune's rounding, not your bug — gate with `--tol 0.001`, not exact-0.

## 7. Validation discipline (never skip)

- **Isolation harness** per source: compare vs Dune/RPC at many sampled points
  (`scripts/compare_debt_sources.py`, `compare_balance_sources.py`,
  `compare_position_balance.py`). Exit non-zero on drift → CI gate.
- **End-to-end (the real gate):** enable the source in a prime, regenerate all
  months, `git diff --stat -- 'settlements/<p>/**/summary.md'` must be **empty**
  (`.xlsx` differ only in the timestamp cell).
- Isolation tests passing ≠ done — the **end-to-end** run catches integration
  bugs the unit level can't (e.g. the block-resolver head-edge race surfaced
  only in a live monthly run).
- Full checklist: `docs/envio/PARITY_REVIEW.md`.

## 8. Gotchas war-chest

- **`ENVIO_API_TOKEN` required** for HyperSync since 2025-11-03 (401 without).
  **No paid plan needed** for reads; self-host or free tier is fine. HyperIndex
  Cloud is a *different* product (hosted indexer) — not needed for HyperSync-direct.
- **HyperIndex can't index Maker's anonymous `LogNote`** (4 indexed topics) — do
  not waste a day on it; go HyperSync-direct.
- **Envio has no `eth_call`.** Not HyperSync, not HyperRPC (logs/blocks/txns
  only). Contract-state pricing (`convertToAssets`, AMM math, NAV oracles) stays
  RPC unless event-reconstructable.
- **Submodule/DR provenance:** `git reset --hard` does **not** update submodule
  working trees — a stale `settle-dr-dune` checkout silently zeroed DR. Run
  `git submodule update --init` and use a **fresh process** (the DR workbook is
  `lru_cache`d per process).
- **`gh pr edit` fails silently** on GitHub's `projectCards` GraphQL deprecation
  — use `gh api --method PATCH repos/…/pulls/<n>` instead.
- **`boundary-only` is often invalid:** the daily supply-side (SDE) integral
  genuinely needs daily state reads — cutting to SoM/EoM changes numbers.
  And per-`(contract, block)` dedup is already done by `@cached` — don't
  re-invent it.
- **Cost/benefit triage** (from experience): debt, balances, block resolution,
  Cat C aTokens = high value, do them. Oracle NAV (tiny volume, `read()` works),
  SSR (one small query), Tier-3 boundary-only = **not worth it / unsafe** — say
  so and move on. `savings_v2` = impossible.

## 9. File map

```
extract/hypersync.py            client (query_logs, block_timestamp, find_block…)
extract/hypersync_store.py      reorg-safe Postgres store
extract/aave_reconstruct.py     wei-exact aToken math (scaled + normalized income)
normalize/sources/hypersync_debt.py            IDebtSource
normalize/sources/hypersync_balances.py        IBalanceSource
normalize/sources/hypersync_block_resolver.py  IBlockResolver (Tier 1)
normalize/sources/hypersync_position_balance.py IPositionBalanceSource (Tier 2, self-verifying)
normalize/sources/hypersync_atoken.py          aToken reconstruction wrapper
db/schema.sql                   hypersync_logs + hypersync_coverage tables
scripts/compare_*_sources.py    parity harnesses
docs/envio/PARITY_REVIEW.md     the review checklist
```
