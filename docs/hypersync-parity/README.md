# HyperSync migration evidence

The JSON files record comparisons against Dune at explicit historical block pins. Venue cutovers are committed individually with their evidence. Shared SSR, debt, idle balances, block resolution and each PSM3 contract have separate evidence.

## Reproducing comparisons

Run from the repository root with the existing virtual environment and provider credentials:

```sh
.venv/bin/python scripts/compare_hypersync_venue.py --prime grove --venue E1 --month 2026-07 --output /tmp/grove-E1.json
.venv/bin/python scripts/compare_hypersync_shared.py --prime grove --month 2026-08 --output /tmp/grove-idle.json
.venv/bin/python scripts/compare_hypersync_shared.py --chain ethereum --month 2026-08 --output /tmp/ethereum-blocks.json
.venv/bin/python scripts/compare_hypersync_psm3.py --chain base --month 2026-08 --output /tmp/base-psm3.json
```

These commands write comparison JSON only; they do not regenerate settlements. Dune execution/read credits and a HyperSync token are required. The existing Postgres log store and extraction cache avoid refetching completed historical inputs.

## What is checked

- Each chain gets its own month-end pin. Transfer balances, directed flows and counterparty classifications are compared across the full daily history from the prime's start date, not only at month end. Quiet dates carry cumulative values forward and have zero daily flow.
- Merkl receipts and Centrifuge asset/share flows are compared in integer token units. LP comparisons include every returned event's block, log index, transaction hash and signed amounts/liquidity delta.
- August 2026 is the main window. Earlier active windows cover Grove E1 July rewards, E3 April rewards, E8 March redemptions, E30 January LP changes, and E33 December 2025 Monad LP changes. Empty histories are distinguished from active examples; E18/E24/E26 also have zero closing balances confirmed over RPC.
- Normalized Dune `DOUBLE` amounts use an absolute tolerance of 0.000001 token units. A failed comparison can pass a separate precision check only if the legacy difference is at most 0.0001 token units **and every raw integer daily/cumulative amount matches exactly**. That evidence retains the failed legacy comparison, its maximum differences, the raw SQL oracle and hashes. Larger differences or any raw mismatch fail.
- Block evidence verifies each Dune end-of-day block's HyperSync timestamp and its immediate successor, with independent HyperSync searches on days 1, 15 and the last day. An empty Dune block result is not a pass.
- PSM3 compares the opening holder/pool share states and every subsequent share event exactly, every daily reserve closing value plus the month-opening anchor, and end-of-month RPC balances/shares. The daily reserve oracle preserves the original Dune transfer running sum while avoiding a multi-million-row API export. HyperSync uses five pinned opening RPC reads (holder shares, total shares and three reserves), then that month's Deposit/Withdraw/Transfer logs. Dune's lifetime running sums independently verify those opening anchors.

Hashes describe normalized comparison inputs. Equal numeric series can have different hashes when Decimal textual scales differ; use the explicit difference checks as the verdict.

## Scope and preserved behavior

The migration changes event acquisition. Contract pricing, NAV, balances used for valuation, and `convertToAssets` calls still use the existing RPC paths. The legacy-named `DuneSavingsV2DeployedSource` already reads `assetsOutstanding()` over RPC and makes no Dune request. Savings V2 position-only venues and explicitly skipped venues do not require Dune event cutovers.

Historical fixture runners and Dune SQL/source implementations remain available as comparison oracles. Explicit caller-supplied sources are preserved. Migrated live paths propagate HyperSync failures rather than interpreting provider outages as zero revenue.

Uniswap V3 retains the existing boundary-based NFT discovery: an NFT opened and closed entirely between both boundaries can be missed by both old and new adapters. Matching event inputs does not resolve that pre-existing limitation.
