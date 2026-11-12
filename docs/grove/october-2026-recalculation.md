# Grove October 2026 recalculation

Originally prepared against main `9b290a3` (including the merged Grove accounting fixes and
bridge-transit idle USDS exemption). PR #261 was independently rebased onto this
main commit; its new head is `372b368`.

The latest update uses main `703c668` with PR #280. This change recalculates Grove October with the
October inputs and a Grove-only runner. The committed summary and workbook are
fresh calculation outputs; prior audit estimates are not reused as results.
No Spark or other prime is calculated, and no API output is published.

## Results

| Ordinary October item | USDS |
|---|---:|
| Venue revenue allocated to Grove, before borrowing costs | 5,661,690.38 |
| Grove borrowing cost / Sky base-rate charge | 4,435,995.50 |
| Grove supply-side net | 1,225,694.88 |
| Grove demand-side revenue (agent rate + DR + Chronicle) | 122,862.65 |
| **Grove ordinary total net** | 1,348,557.53 |
| Sky SDE revenue (JTRSY + BUIDL) | 1,246,963.82 |
| Sky ordinary total (base rate + SDE) | 5,682,959.32 |

The October adjustment includes **partner rebate** of **810,713.50 USDS to Grove**
and **1,246,289.50 USDS to Sky**, calculated by
`scripts/calculate_october_partner_rebate.py` from committed daily evidence.
The six cash receipts total **2,057,003.00 USDS** and are booked once through
MSC adjustments, not added again to ordinary E8/E9 venue income.

Including the separate Jan–September reconciliation, the replacement adjustments
are **822,268.24 supply-side** and **1,135,869.94 Sky-side**. The production
October formula gives **mint 8,866,792 USDS / send 2,170,826 USDS**. Compared with
the previous PR split, mint is unchanged and Grove's unrounded payment decreases
by **105,143.70 USDS** (whole-token send decreases by 105,143).

See [the reproducible recipe](../../reconciliation/2026-10/PARTNER_REBATE_RECIPE.md)
for the historical entitlement assumptions. No payment is executed.

### Main venue contributors to Grove

Gross contributions before the aggregate borrowing charge:

| Venue | USDS |
|---|---:|
| Galaxy warehouse (E42) | 1,894,876.53 |
| JAAA Ethereum + Avalanche (E8/E20) | 1,556,104.30 |
| Base Grove x Steakhouse vault (E43) | 1,298,008.15 |
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

Latest reviewed configuration/evidence SHA-256: `ae0e69cf6e097ecc66b36c07f705d4199d67c01ecb36a7dff16ff4fdd417c5c6`.
The original complete review was revalidated and supplemented with exact
ERC4626 cash controls; the full inventory classifications and balance controls
remain unchanged.

The Grove-only runner writes under `settlements/grove/2026-10/` and retains
existing output protections. `--check-inputs` validates SOFR and DR without
calculating. No API output is published.

## Validation

Input preflight passed. The DR mapping/close, reference-rate/disclosure,
and Basin exemption tests passed: **65 tests**. The fresh monthly run completed; DR, venue/SDE rollups and workbook headline values passed independent checks.

## Valuation and scope notes

The latest replay uses PR #280's exact cash capital for the three Base vaults.
Endpoint valuation is unchanged, so separate `convertToAssets` diagnostics
still differ slightly and are not inserted as balancing adjustments:

| Venue | Current MSC revenue | Cash/convertToAssets diagnostic | Diagnostic minus MSC |
|---|---:|---:|---:|
| E19 | 147,475.47 | 147,431.56 | -43.91 |
| E23 | 97,259.53 | 97,107.08 | -152.45 |
| E43 | 1,298,008.15 | 1,298,280.77 | 272.62 |

The combined October MSC increase is **8,064.96 USDS**. Capital-weighted
averages and total borrowing costs are unchanged. E12 now reports actual
attribution after September 13's principal reduction, with no MSC income change.

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

The latest PR #275 update rebases onto main `703c668` including PR #280.
Only Grove October was replayed, using retained pinned inputs and the verified
unchanged financial snapshot. It recomputes E19/E23/E43 cash income and E12
attribution, independently validates full interest/DR, then renders fresh Grove
reports. No other prime is replayed. Their summaries, workbooks and preview
rows are byte-for-byte unchanged. See the replay evidence and reproduction in
[grove-pr280](../../reconciliation/2026-10/grove-pr280/README.md).
