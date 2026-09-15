# HyperSync migration evidence

The JSON files record comparisons against Dune at explicit historical block pins. Venue cutovers are committed individually with their evidence. Shared SSR, debt, idle balances, block resolution and each PSM3 contract have separate evidence.

All 96 active event venues have passed: Grove 37, Spark 56, Obex 1 and Osero 2. Spark's four PSM3 contracts also have independent passing reports. The six Savings V2 position-only venues and two explicitly skipped Grove venues retain their existing behavior.

Fresh Grove, Spark and Obex settlements use `scripts/run_live_2026.py --primes grove,spark,obex --months 2026-08` with HyperSync and archival RPC credentials; Dune credentials are no longer required by that runner. Osero, Keel and Skybase retain their dedicated runners. The comparison commands below do not write settlement artifacts; the live runners do.

Complete August Obex, Osero and Grove calculations also passed with zero Dune calls in the candidate. Spark's full calculation passed against raw-integer Dune debt/shared balances at the same tolerance, also with zero Dune calls. Its legacy normalized-Dune discrepancy is retained and explained below.

## Reproducing comparisons

Run from the repository root with the existing virtual environment and provider credentials:

```sh
.venv/bin/python scripts/compare_hypersync_venue.py --prime grove --venue E1 --month 2026-07 --output /tmp/grove-E1.json
.venv/bin/python scripts/compare_hypersync_shared.py --prime grove --month 2026-08 --output /tmp/grove-idle.json
.venv/bin/python scripts/compare_hypersync_shared.py --chain ethereum --month 2026-08 --output /tmp/ethereum-blocks.json
.venv/bin/python scripts/compare_hypersync_psm3.py --chain base --month 2026-08 --output /tmp/base-psm3.json
.venv/bin/python scripts/compare_hypersync_settlement.py --prime osero --month 2026-08 --output /tmp/osero-settlement.json
.venv/bin/python scripts/compare_hypersync_settlement.py --prime grove --month 2026-08 --output /tmp/grove-settlement.json
.venv/bin/python scripts/compare_hypersync_settlement.py --prime spark --month 2026-08 --output /tmp/spark-settlement.json
.venv/bin/python scripts/compare_hypersync_settlement.py --prime spark --month 2026-08 --raw-dune --output /tmp/spark-settlement-raw-dune.json
```

These commands write comparison JSON only; they do not regenerate settlements. Dune execution/read credits and a HyperSync token are required. The existing Postgres log store and extraction cache avoid refetching completed historical inputs.

The first historical transfer scan can be slow under a limited provider allowance. `scripts/backfill_hypersync_balances.py --prime spark --chain base --month 2026-08` groups token filters by holder and materializes finalized per-token streams in the existing Postgres cache. It changes no source flags and runs no Dune queries; every venue still needs its own comparison.

## What is checked

- Each chain gets its own month-end pin. Transfer balances, directed flows and counterparty classifications are compared across the full daily history from the prime's start date, not only at month end. Quiet dates carry cumulative values forward and have zero daily flow.
- Merkl receipts and Centrifuge asset/share flows are compared in integer token units. LP comparisons include every returned event's block, log index, transaction hash and signed amounts/liquidity delta.
- August 2026 is the main window. Earlier active windows cover Grove E1 July rewards, E3 April rewards, E8 March redemptions, E30 January LP changes, and E33 December 2025 Monad LP changes. Empty histories are distinguished from active examples; E18/E24/E26 also have zero closing balances confirmed over RPC.
- Per-venue and shared-input checks of normalized Dune `DOUBLE` amounts use an absolute tolerance of 0.000001 token units. A failed comparison can pass a separate precision check only if the legacy difference is at most 0.0001 token units **and every raw integer daily/cumulative amount matches exactly**. That evidence retains the failed legacy comparison, its maximum differences, the raw SQL oracle and hashes. Larger differences or any raw mismatch fail.
- Block evidence verifies each Dune end-of-day block's HyperSync timestamp and its immediate successor, with independent HyperSync searches on days 1, 15 and the last day. An empty Dune block result is not a pass.
- `token-decimals.json` verifies the comparison scales against pinned RPC metadata for every distinct applicable token. `start-boundaries.json` checks that correcting the start floor to the previous second does not introduce omitted holder events into the compared windows; the regression test covers multiple blocks sharing midnight.
- PSM3 compares the opening holder/pool share states and every subsequent share event exactly, every daily reserve closing value plus the month-opening anchor, and end-of-month RPC balances/shares. The daily reserve oracle preserves the original Dune transfer running sum while avoiding a multi-million-row API export. HyperSync uses five pinned opening RPC reads (holder shares, total shares and three reserves), then that month's Deposit/Withdraw/Transfer logs. Dune's lifetime running sums independently verify those opening anchors.
- The full-calculation comparator checks every `MonthlyPnL` dataclass field, including venue components, with an absolute numeric tolerance of 0.000001. Its baseline injects Dune debt, balance and SSR sources and selects Dune venue events. Spark's PSM3 baseline uses Dune lifetime share histories and daily reserve running sums, with the existing valuation arithmetic. That daily oracle rejects intraday or out-of-window reserve requests. Block resolution and RPC valuation are shared; block boundaries have the separate Dune proofs above. The candidate uses the configured sources and forbids all Dune query calls, including cached calls and attempted calls swallowed by fallback handlers. The earlier Obex/Osero reports used the already-verified configured debt source; Grove/Spark explicitly inject Dune debt too.

Hashes describe normalized comparison inputs. Equal numeric series can have different hashes when Decimal textual scales differ; use the explicit difference checks as the verdict.

## Spark's legacy numeric discrepancy

The full legacy-Dune calculation completed with zero Dune calls in its HyperSync candidate, but failed the strict numeric gate on 95 fields. These are daily debt, idle USDS and utilized balances, plus two subsidy balance summaries. The largest difference is 0.000528055075 USD; all four headline amounts are within 0.000001 USD. The failed result is retained in `spark-settlement-legacy-dune-2026-08.json`.

An independent Dune trace query that sums integer wad and returns strings matches HyperSync exactly across all 605 daily debt rows (`spark-debt-raw.json`). HyperSync's raw Art also matches RPC on all 32 opening/daily snapshots (`spark-debt-rpc-2026-08.json`). The 604-row ALM USDS raw-integer comparison already passed (`spark-idle-usds-raw.json`). These checks distinguish numeric differences in the legacy normalized Dune outputs from missing or different events.

The explicit `--raw-dune` mode uses raw Dune debt and configured shared idle balances, with normalization in Python. Other venue queries and PSM valuation arithmetic remain unchanged. It is a separate comparison, not an automatic exception: it keeps the same 0.000001 full-field tolerance and the same ban on Dune calls in the candidate. The legacy failure is never rewritten as a pass.

The complete raw-Dune run passed every field (`spark-settlement-raw-dune-2026-08.json`). The HyperSync output hash is identical in the legacy and raw-Dune runs: only the baseline's numeric representation changed. No fields were excluded and the tolerance was not widened.

## Scope and preserved behavior

The migration changes event acquisition. Contract pricing, NAV, balances used for valuation, and `convertToAssets` calls still use the existing RPC paths. The legacy-named `DuneSavingsV2DeployedSource` already reads `assetsOutstanding()` over RPC and makes no Dune request. Savings V2 position-only venues and explicitly skipped venues do not require Dune event cutovers.

Historical fixture runners and Dune SQL/source implementations remain available as comparison oracles. Explicit caller-supplied sources are preserved. Migrated live paths propagate HyperSync failures rather than interpreting provider outages as zero revenue.

Cutovers are explicit: each venue has `event_source: hypersync`, each PSM3 contract has its own event-source flag, and prime-level `sources` select shared balance/debt/block-resolution families. Changing a venue flag does not replace a caller-injected fixture source.

Uniswap V3 retains the existing boundary-based NFT discovery: an NFT opened and closed entirely between both boundaries can be missed by both old and new adapters. Matching event inputs does not resolve that pre-existing limitation.

Full-calculation validation also identified two unnecessary historical lookup paths. Monthly aToken boundaries now discard out-of-period activity before resolving blocks. Monthly share-flow pricing rebases cumulative inflow at period start, avoiding historical prices that cancel in both period net inflow and time-weighted value; the helper's default still returns full history. Spark's migrated bridged-sUSDS pricing now uses the configured block resolver, avoiding the legacy RPC resolver's opportunistic Dune lookup.

Post-review historical regressions: transfer sources now return empty event histories without requesting token decimals at predeployment pins. The production Spark S65 January share-flow helper was rerun successfully against live HyperSync at Ethereum block 24358292 (the token's `decimals()` returns `0x` at that pin). PSM3 pool histories accept an empty opening `totalShares()` response as zero only after an RPC bytecode check confirms the contract did not exist. Regression tests cover both a wholly predeployment window and subsequent deposits in the deployment month, and verify that provider failures and empty responses from deployed PSMs still raise. These targeted checks supplement the August comparisons; they are not full historical monthly comparisons.
