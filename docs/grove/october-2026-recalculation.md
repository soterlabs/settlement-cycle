# Grove October 2026 recalculation

Prepared against main `9b290a3` (including the merged Grove accounting fixes and
bridge-transit idle USDS exemption). PR #261 was independently rebased onto this
main commit; its new head is `372b368`.

This dedicated change recalculates Grove October using latest main, with the
October inputs and a Grove-only runner. The committed summary and workbook are
fresh calculation outputs; prior audit estimates are not reused as results.
No Spark or other prime is calculated, and no API output is published.

## Results

| Ordinary October item | USDS |
|---|---:|
| Venue revenue allocated to Grove, before borrowing costs | 5,653,625.42 |
| Grove borrowing cost / Sky base-rate charge | 4,435,995.50 |
| Grove supply-side net | 1,217,629.92 |
| Grove demand-side revenue (agent rate + DR + Chronicle) | 122,862.65 |
| **Grove ordinary total net** | 1,340,492.57 |
| Sky SDE revenue (JTRSY + BUIDL) | 1,246,963.82 |
| Sky ordinary total (base rate + SDE) | 5,682,959.32 |

The approved historical bridge in `config/sky_total.yaml` adds **915,857.20**
to Grove supply-side revenue and **1,062,615.74** to Sky's share (partner rebate
1,141,145.80 less historical CoF credit 78,530.06). These are separate from
October-earned revenue; the six partner receipts are not added as venue income.

Grove supply-side after the partner adjustment is **2,133,487.12 USDS**. Including demand-side revenue, Grove's total is **2,256,349.77 USDS**.

Applying the existing October MSC formula and whole-USDS rounding gives **mint 8,879,062 USDS** and **send 2,256,350 USDS** for Grove. These are Grove-only derived instructions; they do not assert an executed settlement or a consolidated all-prime MSC reconciliation.

### Main venue contributors to Grove

Gross contributions before the aggregate borrowing charge:

| Venue | USDS |
|---|---:|
| Galaxy warehouse (E42) | 1,894,876.53 |
| JAAA Ethereum + Avalanche (E8/E20) | 1,556,104.30 |
| Base Grove x Steakhouse vault (E43) | 1,274,828.98 |
| Securitize AAA CLO / STAC (E7) | 427,367.20 |
| Galaxy Arch CLO (E21) | 142,165.55 |

## Inputs

- `settle-dr-dune`: `d53dd1317bd8f52f22a3400aebcb5a095689a812`, merged PR #34.
- October DR CSV and ref-code rollups: 210 rows, all 32 shards complete.
  Grove DR: **$22,572.84943285862405046758602**.
- The October-only DR workbook is derived from the published rollup because the
  upstream comparison workbook ends in September. The manifest documents its
  source and hashes. It is not represented as an upstream October workbook.
- Official October SOFR snapshot: full period coverage, supplied explicitly to
  avoid carrying the repository's older last-known rate through October.
- Approved partner adjustments remain sourced from the merged configuration;
  the partner receipts must not also be added as venue interest.

## Completed transfer review

The required local review is complete at
`.msc-transfer-reviews/grove/2026-10/review.json`, with evidence/config hashes,
zero unclassified groups and the October boundary blocks used by the run.

- Primary ALMs on Ethereum, Base, Avalanche C, Plume and Monad, plus Ethereum
  Diamond PAU: 1,850 transfers in 56 reviewed groups. Includes specific spam
  exclusions, CCTP proofs, partner receipts, vault receipt/cash legs and swaps.
- Current E43/E44 configuration and approved E36 EOA boundaries are applied.
  Downstream FalconX operations are not separate MSC expenses.
- Additional Basin scan: both Basins and their two pockets/escrows; 20 joined
  logs in two transactions, covering four token transfers in two groups.
  These are common-pool JTRSY/USDC exchanges, with no change to PAU ownership;
  they are not separate PAU capital movements or cash income.
- All 16 original token balance controls and 12 additional Basin controls have
  zero residual. Basin PAU shares and USDS balances agree at both boundaries.
- Enumerated Ethereum NFT ownership is verified at both boundaries: E12 IDs
  1192575/1353600 and E44 ID 1352494. No in-month ownership transfers; liquidity
  changes and fees are valued separately by the settlement pipeline.
- Operator instruction November 10: acknowledge the 76,133.73 AVAX receipt as
  outside MSC scope (MSC concerns stablecoins). No AVAX income/principal amount
  is inferred or included. Stablecoin-backed receipt positions remain reviewed.

Review record SHA-256: `52a87a540e0eeba81c477341647d90cef744423f073d8cc3fd294f6b0eba2797`.

The Grove-only runner writes under `settlements/grove/2026-10/` and retains
existing output protections. `--check-inputs` validates SOFR and DR without
calculating. No API output is published.

## Validation

Input preflight passed. The DR mapping/close, reference-rate/disclosure,
and Basin exemption tests passed: **65 tests**. The fresh monthly run completed; DR, venue/SDE rollups and workbook headline values passed independent checks.

## Valuation and scope notes

The committed amounts reproduce current main's pricing and capital methodology.
The independent cash audit from PR #261 is a separate control and still shows
these differences; they have not been inserted as balancing adjustments:

| Venue | Current MSC revenue | Cash/convertToAssets diagnostic | Diagnostic minus MSC |
|---|---:|---:|---:|
| E19 | 150,683.03 | 147,431.56 | -3,251.47 |
| E23 | 109,166.19 | 97,107.08 | -12,059.10 |
| E43 | 1,274,828.98 | 1,298,280.77 | 23,451.79 |

The review identifies the cash and receipt routes; it does not turn these
valuation differences into approved fee/income corrections. This PR recalculates the existing
method and retains these diagnostics for accounting review.

The 39 authenticated AUSD/USDC swaps have net par execution spread
**-4,332.476098 USDS**. Current E14 configuration explicitly treats the operational
conversion spread as reporting-only under the Grove agreement; it is not an MSC
expense or an extra LP-yield adjustment. E12/E44 LP returns remain in MSC.

October 31 is a Saturday: carrying October 30's official SOFR through that day
passes the configured calendar coverage validation. The generic last-day-rate
warning does not indicate a missing October business-day observation.

A second full Grove-only run into an independent directory reproduced every
result, venue row, daily Sky cost and summary. Workbook values match across all
seven sheets; generated timestamps differ. The saved-output guard also rejects
overwriting the existing close without an explicit override.
