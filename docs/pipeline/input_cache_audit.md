# Persistent revenue inputs — PRD step 2

Scope: the configured prime-revenue path in `compute_monthly_pnl`, including
`as_of`. The audit follows code and configured sources, not old architecture
diagrams. This delivers persistent input reuse; it does not add a scheduler,
result table, incremental revenue arithmetic, or provider-plan budgets.

## Cache contract

Daily workers set `SETTLE_REQUIRE_POSTGRES=1` and `DATABASE_URL`. Apply
`db/schema.sql` before starting them. Missing connections and failed raw-cache
reads/writes raise `PersistenceError`; a warm local file cannot hide a failed
required database write. Required persistence and input-finality failures also
fail the calculation if an existing source fallback catches the exception. A local hit absent from Postgres is promoted before
returning. Without required mode the existing optional/local-only behavior is
retained for developer tools and ordinary monthly workflows.

As-of runs enter a run-local finalized-input scope. Every decorated read with
`chain` and `block` parameters uses the `finalized.v2.<revision>.<source>`
namespace and canonical bound arguments (including defaults). Positional and
keyword forms therefore share a key. These reads cannot consume legacy cache
entries that may have been populated before finality or by old zero-on-error
implementations. Each chain's head is checked once per calculation and the
configured reorganization margin is subtracted; reads above that conservative
ceiling fail before extraction. Pin certification remains a fresh check.

Raw RPC replies must contain complete ABI words. Empty replies are accepted as
predeployment only after a pinned bytecode check; empty replies from deployed
contracts fail. Exceptions are never cached. If a nested cached call fails and
an outer helper catches it as zero/None, the outer result is not persisted.
None capability results are not persisted in this scope. Real ABI zeros and
confirmed predeployment zeros remain cacheable. This changes persistence
semantics, not existing compute-layer warning/carry-forward policies.

`Vat.ilks` additionally requires its complete five-word tuple before raw caching.
The v2 namespace excludes snapshots written by the earlier word-alignment-only
validation, including decoded fallback rates. Other ABI-specific decoding still
belongs to the corresponding extractors.

## Audited extraction paths

| Input / consumers | Provider and persistence | Cache identity / extension |
|---|---|---|
| Allocator Vat debt, including extra ilks | `hypersync_debt` → `hypersync_store`; daily `ilk_rate` → RPC cache | Chain + event selection + fields; missing event intervals only. Rate is chain + Vat + ilk + block. |
| SSR changes | `hypersync_ssr` → log store | Savings contract + selector stream; historical rows reused through the new cutoff. |
| ALM/subproxy transfers, capital flows, cash distributions, EOA relays, Chronicle Farm | `hypersync_balances` → log store | Token/holder/direction selections. Empty complete ranges count as coverage. Superset reuse requires complete coverage. |
| ERC-4626 deposits/withdrawals, aToken and redemption-queue events | `hypersync_venue_events`, Aave reconstruction → log store | Contract + topics; new cutoff extends coverage. |
| Position balances and savings-vault underlying | `hypersync_position_balance` → log store + RPC verification | Transfer streams and block-pinned RPC snapshots. In-memory classification may repeat after restart, but its inputs persist. |
| PSM3 holder shares, pool shares, reserves | `hypersync_psm3` → log store + opening RPC snapshots | Stable contract/holder/token streams. Opening seeds and daily PPS persist by block. Internal HyperSync date lookups inherit finalized scope. |
| Uniswap V3 liquidity events | `hypersync_lp` → log store | Separate stream per NFT, so a newly acquired NFT does not invalidate existing positions' coverage. |
| Uniswap V3 Collect/Decrease fee accounting | `uniswap_v3.read_fee_collections` → log store | NFT + event topics + transaction-hash field set. Previously bypassed the store; now incremental. |
| Uniswap V4 liquidity events | `hypersync_lp` → log store | Pool/position-manager selection, with transaction hashes. |
| RWA vault-priced redemption transfers | `positions._vault_priced_redemptions_by_date` → log store | Token + holder + requested fields. Previously bypassed the store. Cross-chain valuation timestamps use finalized resolution. |
| ERC-20 balances/supplies, ERC-4626 PPS, Vat rates, PSM state | `rpc` primitives + normalized wrappers → raw-data cache | Chain + address + calldata/arguments + block, finalized namespace for as-of runs. |
| Curve/V3/V4 positions, pool state, ticks and oracle NAV | `curve`, `uniswap_v3`, `uniswap_v4`, `oracles/*` → raw-data cache | Same block-pinned contract; structured payloads use existing lossless serialization. |
| Date ↔ block resolution | Finalized HyperSync helpers → raw-data cache | Chain + target timestamp/block + finality margin; successful finalized results only. Legacy internal source lookups are redirected in as-of scope. |
| Source configuration, notional/SDE/rate schedules and overrides | Local versioned YAML | Read locally; no provider cost and no database input cache needed. Configuration version belongs to eventual result identity. |
| DR / active GAR | Monthly workbook / consolidated artifact | Partial-month DR excluded; active monthly-only GAR rejected by step 1. No daily external query added here. |

All active configured event venues use HyperSync. Legacy Dune adapters, manual
reconciliation/comparison scripts, RPC-only event adapters, CoinGecko spot
pricing, consolidated Sky/SBE/non-MSC reports and the separate TMF job are
outside this prime-revenue scope. In particular their direct HyperSync queries
are not evidence that the configured prime-revenue path bypasses persistence.

## Event coverage and completeness

`hypersync_logs` stores rows and `hypersync_ranges` stores append-only inclusive
coverage intervals. Readers merge adjacent/overlapping intervals and fetch only
uncovered ranges; disjoint backfills remain separately reusable. The legacy
`hypersync_coverage` table is still read, and its single-range view is kept
honest for older workers. Concurrent writers may add redundant intervals but
cannot overwrite away another interval or invent an unfetched gap.

Rows are written before their interval claim. If a worker stops between those
writes, retry re-fetches the unclaimed interval; idempotent inserts deduplicate
rows. Previously completed intervals survive failures on later intervals.
Empty responses establish coverage only if pagination reaches the requested
end. Missing/stalled pagination fails even if archive-height metadata is absent.
Only blocks below the most conservative archive head observed across all pages,
minus the reorganization margin, are persisted. A live tail is returned but not
claimed. As-of pins separately require finalized boundaries.

## Corrections and operation

- Use one consistent `SETTLE_INPUT_REVISION` across workers (default `0`). On a
  confirmed upstream-data correction or a bad historical cache, change it to a
  new identifier and rerun. Both raw-data keys and event-stream keys change;
  the old revision remains available for audit. This is independent of code
  or calculation version and deliberately refetches corrected inputs.
- Bootstrap: `psql "$DATABASE_URL" -f db/schema.sql`.
- Worker environment: `DATABASE_URL`, `SETTLE_REQUIRE_POSTGRES=1`, provider
  credentials, and an optional local `SETTLE_CACHE_DIR`. The local directory
  may be empty or ephemeral; Postgres is the required durable layer.
- `SETTLE_NO_CACHE=1` and `HYPERSYNC_NO_STORE=1` conflict with required mode.
- `HYPERSYNC_REORG_MARGIN` must be nonnegative. A cache is only as final as this
  configured policy; changing finality assumptions or correcting old data
  requires a new input revision.
- The existing Postgres JSON/pickle encoding is unchanged; deploy compatible
  Python types when sharing structured cached values between workers.

## Validation and next steps

Unit tests exercise stale legacy entries, argument normalization, correction
revisions, unfinalized reads, malformed/empty RPC results, fallback zeros,
required database failures, pagination completeness and disjoint coverage.
The new Postgres integration job runs fresh Python processes with separate
local cache directories and real isolated schemas. It verifies raw snapshot
reuse, identical-range reuse, range extension, disjoint backfills, gap filling,
and recovery after rows are written but before coverage is claimed. Provider
responses in that test are deterministic, not live-chain evidence.

PRD step 3 still requires end-to-end provider-request assertions for every prime
on same-date reruns, including the legitimate freshness/capability probes.
Step 4 measures actual next-day traffic and runtime. Their acceptance budgets
must come from those runs, not from the cache primitives tested here.
