# PRD — Resolve remaining allocation funding splits

Status: deferred next workstream. Snapshot: August 2026. No new funding-source investigation is part of the monthly API change.

## Objective

Calculate the Sky-funded part of each allocation on every reporting day, so monthly borrowing costs and net yields can be completed. Preserve the operator assumption that principal is Sky-funded except Spark Savings funding, and never charge borrowing costs on retained/reinvested gains.

## What is and is not unresolved

Capital amounts and movements can already be measured. The remaining question is the mix of Sky borrowing and Savings funding, including later refinancing. A Savings dependency is a conservative indication that Savings money could reach the account; it is not a measured Savings balance and does not mean the whole position is saver-funded.

At least 99% explained origin at August-end for S1, S34 and S23 does not prove the correct daily funding mix throughout August. Reuse those receipt proofs; do not discard them or restart chain extraction.

## Configured Spark allocations requiring a funding split

Average original principal below is the diagnostic capital basis, not a certified Sky-only balance or NAV. Sorted by August average principal.

| Venue | Allocation | August average original principal (USD) |
|---|---|---:|
| S1 | Spark USDS (SparkLend spToken) | 764,320,006.90 |
| S32 | sUSDS raw / POL (ALM — Cat B 4626, demand-side spread) | 665,009,738.44 |
| S3 | Spark USDT (SparkLend spToken) | 329,358,955.84 |
| S4 | Spark DAI (SparkLend spToken) | 289,447,408.65 |
| S23 | Anchorage tri-party loan escrow (USDC pass-through, ~$0 balance) | 235,264,418.08 |
| S28 | PYUSD raw (ALM idle — $677M as of 2026-04) | 225,861,956.37 |
| S5 | Spark PYUSD (SparkLend spToken) | 100,353,327.43 |
| S61 | Spark.fi PYUSD Reserve Uniswap V4 (PYUSD/USDS) | 99,395,193.53 |
| S34 | Spark USDC Vault (Morpho, Base) | 98,886,579.50 |
| S62 | Spark.fi USDT Reserve Uniswap V4 (USDT/USDS) | 49,751,640.69 |
| S64 | RLUSD raw (ALM idle — $251.7M as of 2026-08) | 30,587,887.30 |
| S65 | Spark Blue Chip USDT Vault (Morpho — 2026-06 redeployment) | 11,253,091.98 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | 4,232,441.16 |
| S2 | Spark USDC (SparkLend spToken) | 4,141,253.59 |
| S66 | Spark.fi RLUSD Reserve Uniswap V4 (RLUSD/USDS) | 3,798,452.54 |
| S45 | USDC raw (Arbitrum — ALM idle) | 2,145.46 |
| S55 | USDC raw (Avalanche-C — ALM idle) | 1,198.23 |
| S49 | USDC raw (Optimism — ALM idle) | 972.68 |
| S39 | USDC raw (Base — ALM idle) | 807.64 |
| S53 | USDC raw (Unichain — ALM idle) | 569.00 |
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | 36.76 |
| S9 | Aave Ethereum USDT (aToken) | 5.87 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | 3.88 |
| S11 | Spark Blue Chip USDT Vault (Morpho — pre-2026-06 deployment) | 1.75 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | 0.82 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | 0.15 |
| S13 | Spark USDS Vault (Morpho) | 0.00 |
| S6 | Aave Ethereum Lido USDS (aToken) | 0.00 |

## Additional Spark custody/bridge accounts

These are intermediate accounts, not additional investments to charge twice. Preserve the funding mix across the route and carry it into its final allocation.

| Tracking account | August average original principal (USD) |
|---|---:|
| S_OPTIMISM_NATIVE_PENDING_JUNE_1 | 9,990.21 |
| S_UNICHAIN_NATIVE_PENDING_JUNE_1 | 9,974.90 |
| S_BASE_JUNE_NATIVE_PENDING_1 | 9,924.90 |
| S_B2C2_ENTRY | 1,451.00 |
| S_BINANCE_OTC | 1,290.32 |
| S_PAXOS_PYUSD_USDC | 12.03 |

## Savings source accounts and Grove

- Funding source liabilities: S56 (Ethereum USDC), S57 (USDT), S59 (PYUSD), S60 (Avalanche USDC). Verify scope separately for configured S58 (ETH) and S63 (Robinhood USDG); a configured liability row alone is not evidence of an August funding flow.
- Grove has no Savings-funding exception under the accepted operator assumption. Its PAU/Basin/Paxos accounts use existing rules; they are not funding-origin blockers.
- FalconX/other authorized EOAs remain allocation boundaries. Do not trace commingled balances inside those wallets.
- Missing revenue for an additional tracking row is a revenue-coverage issue, not an unknown capital source. Do not classify it as zero revenue.
- The separately deferred Grove test-payment/purpose case remains in the local PRD_reconciliation_msc14.md. This PRD does not reopen or silently resolve it.

## Work order

1. Reuse saved capital histories, January opening checkpoints, Savings Take/return evidence and existing receipt-proven routes. Prioritize material rows in the table.
2. Quantify the original principal split per day, separately tracking Sky, Savings and earnings. Preserve independently proven intra-transaction routes rather than pooling all tokens in a spell.
3. Account for Sky refinancing a Savings repayment: investments can become Sky-funded without receiving a new asset deposit. Separate VSR interest from returned saver principal; retain the documented repayment policy explicitly.
4. Carry funding through withdrawals, conversions, bridges and redemption claims. Gains do not become borrowed principal when reinvested. Clear dependency only on an evidenced full exit or an evidenced refinancing/split.
5. Replace conservative dependencies with measured splits venue by venue; rerun only affected monthly analytics, using existing revenue and rates.

## Acceptance criteria

- Every daily component is nonnegative and their sum reconciles to the measured capital basis. A single month-end percentage is insufficient.
- Cash/receipt evidence supports any upgraded classification; alternative proofs are not added twice.
- Unknown costs/net yields remain null in the API, with explicit status. Zero means an actual zero, exemption or no principal.
- Idle non-USDS balances pay the applicable borrowing rate on Sky-funded principal. Apply only existing USDS/SDE exemptions, with effective dates and ilk limits.
- Global debt, MSC-related debt, prime financing adjustments and allocation costs remain distinct. Do not rescale allocation costs merely to force a total to match.
- Published January-August settlements and existing API revenue are not restated. New monthly analytics revisions retain older results.

## Existing evidence to reuse

- docs/spark/anchorage-origin-99-2026-10-28.md
- docs/spark/base-morpho-origin-99-2026-10-28.md
- docs/spark/spusds-receipt-origins-2026-10-28.md
- docs/spark/savings-funding-inventory-2026-10-08.md
- docs/spark/savings-principal-policy-2026-10-09.md
- reconciliation/allocation_capital_readiness_2026_08.json.gz (daily capital and August dependency dates)
- reconciliation/funding_origin_monthly_2026_01_08.json (month-end origin evidence)
