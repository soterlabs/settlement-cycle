# PRD — Resolve remaining allocation funding splits

Status: deferred next workstream. Snapshot: August 2026. No new funding-source investigation is part of the monthly API change.

## Current PR boundary

PR #215 delivers monthly borrowing costs per allocation for **Sky-funded principal**, using the operator-confirmed Sky-except-Savings assumption. See [the current PR scope](PR215_scope.md). The investigations below are deferred; they are not required to calculate or publish costs for already usable allocations.

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

## Retained code: explicit references and intended use

All paths below are intentionally retained in place. Moving them now would invalidate imports, fixture references and saved source-hash/reproduction manifests. They are retained for the next funding workstream or to reproduce the capital history already used by this PR; they are not automatically executed by normal daily/monthly revenue runs.

### Current calculation and publication path

| File | Role |
|---|---|
| [scripts/report_allocation_capital_readiness.py](../scripts/report_allocation_capital_readiness.py) | Current assumption-based capital and monthly cost calculation; uses existing movements, not proof of every original mint. |
| [scripts/report_allocation_yields.py](../scripts/report_allocation_yields.py) | Current revenue reuse and gross/net yield calculation. |
| [scripts/publish_monthly_allocations.py](../scripts/publish_monthly_allocations.py) | Current reviewed-result publication without replay or revenue regeneration. |
| [src/settle/api/allocations.py](../src/settle/api/allocations.py) | Read-only monthly endpoint. |
| [src/settle/revenue/allocations.py](../src/settle/revenue/allocations.py) | Result consistency checks and immutable revisions. |
| [src/settle/compute/allocation_financing.py](../src/settle/compute/allocation_financing.py) | Only annualized_yield is shared by the current path. The full financing replay remains explicit diagnostic/research functionality. |
| [src/settle/normalize/allocation_history_cache.py](../src/settle/normalize/allocation_history_cache.py) | Read existing normalized histories; preserve checkpoints and event metadata. |
| [src/settle/compute/spark_anchorage_correction.py](../src/settle/compute/spark_anchorage_correction.py) | Retain the already-reviewed July principal correction. |
| [src/settle/compute/spark_anchorage_scenarios.py](../src/settle/compute/spark_anchorage_scenarios.py) | Reuse the already-accepted full-principal return policy; do not reopen the decision. |

### Deferred research and diagnostic entry points

These tools are useful for closing the listed Savings splits, reproducing receipts, or validating inherited histories. They are not the current monthly publication gate.

| Retained file | Purpose |
|---|---|
| [scripts/audit_allocation_cash_sources.py](../scripts/audit_allocation_cash_sources.py) | Trace isolated vault deposits through empty ALM cash and authenticated bridges. |
| [scripts/audit_allocation_financing_bridge.py](../scripts/audit_allocation_financing_bridge.py) | Read-only numerical bridge, including unresolved modeled allocation costs. |
| [scripts/audit_allocation_origin_coverage.py](../scripts/audit_allocation_origin_coverage.py) | Rank audited allocation capital not yet attributed to original Sky draws. |
| [scripts/audit_allocation_positions.py](../scripts/audit_allocation_positions.py) | Allocation-local contributed capital, independent of other venues' debt. |
| [scripts/audit_bridged_allocation_origins.py](../scripts/audit_bridged_allocation_origins.py) | Trace original draws through authenticated CCTP claims and allocation cash. |
| [scripts/audit_bridged_vault_funding.py](../scripts/audit_bridged_vault_funding.py) | Attribute a newly opened vault's capital to authenticated funding origins. |
| [scripts/audit_cash_origin_windows.py](../scripts/audit_cash_origin_windows.py) | Borrowed basis and identifiable gains in a bounded, mixed cash window. |
| [scripts/audit_facility_funding.py](../scripts/audit_facility_funding.py) | Audit allocation entrypoints independently of a full funding replay. |
| [scripts/audit_grove_execution_shortfalls.py](../scripts/audit_grove_execution_shortfalls.py) | Explain raw capital-replay residuals using actual swap execution evidence. |
| [scripts/audit_grove_lp_residuals.py](../scripts/audit_grove_lp_residuals.py) | Read-only proof of E11 LP valuation differences; never modify capital or CoF. |
| [scripts/audit_origin_custody_routes.py](../scripts/audit_origin_custody_routes.py) | Separate registered same-venue custody legs before diagnostic origin pooling. |
| [scripts/audit_scoped_allocation_origin.py](../scripts/audit_scoped_allocation_origin.py) | Allocation funding origin with upstream accounts, custody ranking and monthly snapshots. |
| [scripts/audit_scoped_borrowing_origins.py](../scripts/audit_scoped_borrowing_origins.py) | Original borrowing attribution within explicitly selected allocation accounts. |
| [scripts/audit_spark_aave_reward_claim.py](../scripts/audit_spark_aave_reward_claim.py) | Authenticate the October 8, 2025 aUSDS reward receipt, without editing a replay. |
| [scripts/audit_spark_anchorage_draw_routes.py](../scripts/audit_spark_anchorage_draw_routes.py) | Receipt-proven Anchorage funding; existing principal-return policy is unchanged. |
| [scripts/audit_spark_anchorage_scenarios.py](../scripts/audit_spark_anchorage_scenarios.py) | Quantify unconfirmed Anchorage cash-principal scenarios, without replaying. |
| [scripts/audit_spark_anchorage_upstream.py](../scripts/audit_spark_anchorage_upstream.py) | Carry separately traced cash, vault and CCTP funding into Anchorage deposits. |
| [scripts/audit_spark_cctp_draw_routes.py](../scripts/audit_spark_cctp_draw_routes.py) | Prove fresh USDC borrowing paid into already authenticated CCTP v1 claims. |
| [scripts/audit_spark_draw_routes.py](../scripts/audit_spark_draw_routes.py) | Receipt-authenticated USDS draw routes for the funding-origin diagnostic only. |
| [scripts/audit_spark_ethena_execution.py](../scripts/audit_spark_ethena_execution.py) | Prove Spark's Ethena mint/redeem shortfalls from events and actual cash. |
| [scripts/audit_spark_execution_residuals.py](../scripts/audit_spark_execution_residuals.py) | Separate witnessed execution costs from remaining transaction discrepancies. |
| [scripts/audit_spark_legacy_curve_swaps.py](../scripts/audit_spark_legacy_curve_swaps.py) | Read-only witnesses for Spark's legacy and RLUSD Curve swaps. |
| [scripts/audit_spark_native_return_origins.py](../scripts/audit_spark_native_return_origins.py) | Carry proven L2 funding into two authenticated July sUSDS returns. |
| [scripts/audit_spark_ordered_draws.py](../scripts/audit_spark_ordered_draws.py) | Authenticate Spark's gross USDS draw -> sUSDS route despite later repayments. |
| [scripts/audit_spark_par_swaps.py](../scripts/audit_spark_par_swaps.py) | Audit proven stablecoin swaps and optionally emit historical tracing rules. |
| [scripts/audit_spark_savings_funding.py](../scripts/audit_spark_savings_funding.py) | Identify Spark Savings cash funding separately from Sky draws and revenue. |
| [scripts/audit_spark_savings_liability.py](../scripts/audit_spark_savings_liability.py) | Reconcile Spark Savings accrued liabilities without assigning Sky funding. |
| [scripts/audit_spark_savings_origin.py](../scripts/audit_spark_savings_origin.py) | Attribute sUSDS contributions using separate Savings and gross Sky draw evidence. |
| [scripts/audit_spark_savings_period.py](../scripts/audit_spark_savings_period.py) | Read-only period accrual from two independently checked Savings boundaries. |
| [scripts/audit_spark_savings_principal.py](../scripts/audit_spark_savings_principal.py) | Diagnostic proportional Savings repayment policy; never settlement revenue. |
| [scripts/audit_spark_spusds_draws.py](../scripts/audit_spark_spusds_draws.py) | Receipt-proven Sky draws into spUSDS, independently of Savings token legs. |
| [scripts/audit_spark_spusds_returns.py](../scripts/audit_spark_spusds_returns.py) | Carry proven spUSDS borrowing through isolated USDS withdrawal/deposit routes. |
| [scripts/audit_spark_subproxy_reserves.py](../scripts/audit_spark_subproxy_reserves.py) | Identify reserve earnings carried through SubProxy, excluding tiny seed mints. |
| [scripts/audit_spark_susds_swaps.py](../scripts/audit_spark_susds_swaps.py) | Read-only Curve execution audit using independently reconstructed sUSDS NAV. |
| [scripts/audit_spark_uscc_history.py](../scripts/audit_spark_uscc_history.py) | Read-only inventory of Spark's USCC issuer boundary; no inferred cash links. |
| [scripts/audit_spark_usdc_draw_routes.py](../scripts/audit_spark_usdc_draw_routes.py) | Prove fresh borrowing in selected USDC payments through USDS/DAI and PSM. |
| [scripts/audit_spark_usdc_origin_graph.py](../scripts/audit_spark_usdc_origin_graph.py) | Trace S34 origin through Spark's USDC positions, message claims and PSM3 shares. |
| [scripts/audit_spark_v4_fee_witness.py](../scripts/audit_spark_v4_fee_witness.py) | Read-only proof of Spark S61 fees from independent position checkpoints. |
| [scripts/audit_spark_vat_accrual.py](../scripts/audit_spark_vat_accrual.py) | Independently reconstruct Spark cash debt and non-cash Vat rate accrual. |
| [scripts/audit_unallocated_borrowing_costs.py](../scripts/audit_unallocated_borrowing_costs.py) | Read-only monthly costs actually carried by transaction outflow accounts. |
| [scripts/collect_spark_origin_receipts.py](../scripts/collect_spark_origin_receipts.py) | Select sUSDS origin evidence across month ends, not only August survivors. |
| [scripts/compare_spark_financing_snapshots.py](../scripts/compare_spark_financing_snapshots.py) | Compare frozen Spark financing diagnostics against unchanged settlement controls. |
| [scripts/patch_spark_savings_funding.py](../scripts/patch_spark_savings_funding.py) | Attach independently audited Savings funding to a frozen diagnostic history. |
| [scripts/repair_spark_base_morpho_withdrawals.py](../scripts/repair_spark_base_morpho_withdrawals.py) | Refresh exact cash marks for already-recognized Base Morpho fee withdrawals. |
| [scripts/repair_spark_ethereum_fsusds.py](../scripts/repair_spark_ethereum_fsusds.py) | Repair Ethereum fsUSDS tracing using verified sUSDS assets and cash. |
| [scripts/repair_spark_fsusds_capital.py](../scripts/repair_spark_fsusds_capital.py) | Repair diagnostic fsUSDS marks using exact, paired sUSDS cash movements. |
| [scripts/repair_spark_mixed_deposits.py](../scripts/repair_spark_mixed_deposits.py) | Repair frozen diagnostic sUSDS deposits using complete share-transfer logs. |
| [scripts/repair_spark_morpho_fee_history.py](../scripts/repair_spark_morpho_fee_history.py) | Repair only proven Ethereum Morpho fee legs in an immutable tracing snapshot. |
| [scripts/report_funding_origin_monthly.py](../scripts/report_funding_origin_monthly.py) | Month-end funding-origin snapshots and complete configured-venue audit inventory. |
| [scripts/validate_allocation_capital.py](../scripts/validate_allocation_capital.py) | Replay allocation capital without regenerating settlement artifacts. |
| [scripts/validate_allocation_financing.py](../scripts/validate_allocation_financing.py) | Compare allocation costs to a read-only published settlement control. |

### Retained capital primitives, custody adapters and legacy origin models

The normalization/custody adapters remain useful for measuring capital and excluding known income. The origin/refinancing models remain available to explicit research calls. Keeping both avoids losing the evidence behind the frozen capital inputs.

| Retained module | Purpose |
|---|---|
| [src/settle/compute/allocation_capital.py](../src/settle/compute/allocation_capital.py) | Borrowed-principal attribution; deliberately independent of settlement debt. |
| [src/settle/compute/allocation_external_funding.py](../src/settle/compute/allocation_external_funding.py) | Carry non-Sky funding alongside, but never inside, Sky borrowing costs. |
| [src/settle/compute/allocation_reconciliation.py](../src/settle/compute/allocation_reconciliation.py) | Read-only per-ilk reconciliation to pinned debt and settlement controls. |
| [src/settle/compute/allocation_uncertainty.py](../src/settle/compute/allocation_uncertainty.py) | Conservative funding envelopes for read-only allocation diagnostics. |
| [src/settle/compute/executed_spell_capital.py](../src/settle/compute/executed_spell_capital.py) | Execution-specific acquisition costs and links, applied before capital replay. |
| [src/settle/compute/grove_agora_incentives.py](../src/settle/compute/grove_agora_incentives.py) | Classify the existing E38 cash-incentive policy in saved Grove histories. |
| [src/settle/compute/grove_agora_redemptions.py](../src/settle/compute/grove_agora_redemptions.py) | Reviewed Agora AUSD redemptions: preserve capital until USDC arrives. |
| [src/settle/compute/grove_apollo_cash_settlements.py](../src/settle/compute/grove_apollo_cash_settlements.py) | Reviewed Apollo cash paid before corresponding Plume share cancellations. |
| [src/settle/compute/grove_basin_capital.py](../src/settle/compute/grove_basin_capital.py) | Reviewed Basin share events for previously pinned Grove histories. |
| [src/settle/compute/grove_buidl_partial_payments.py](../src/settle/compute/grove_buidl_partial_payments.py) | Six reviewed issuer advances preceding their larger BUIDL settlement. |
| [src/settle/compute/grove_buidl_subscriptions.py](../src/settle/compute/grove_buidl_subscriptions.py) | Exact Grove BUIDL subscription links, independent of revenue recognition. |
| [src/settle/compute/grove_cash_distributions.py](../src/settle/compute/grove_cash_distributions.py) | Apply the existing configured Galaxy yield classification to saved histories. |
| [src/settle/compute/grove_cctp_v2_capital.py](../src/settle/compute/grove_cctp_v2_capital.py) | Reviewed Grove CCTP v2 routes, authenticated by Circle message nonce. |
| [src/settle/compute/grove_curve_swap_gains.py](../src/settle/compute/grove_curve_swap_gains.py) | Reviewed actual Curve swap gains for previously pinned Grove histories. |
| [src/settle/compute/grove_falconx_test_refund.py](../src/settle/compute/grove_falconx_test_refund.py) | Recognize the November 24 FalconX onboarding test's two USDC returns. |
| [src/settle/compute/grove_galaxy_arch_capital.py](../src/settle/compute/grove_galaxy_arch_capital.py) | Grove Galaxy ARCH funding at the issuer's deposit and return boundaries. |
| [src/settle/compute/grove_historical_capital.py](../src/settle/compute/grove_historical_capital.py) | Exact Grove issuer executions that bypass ordinary vault claim events. |
| [src/settle/compute/grove_merkl_rewards.py](../src/settle/compute/grove_merkl_rewards.py) | Classify two reviewed wrapper claims in pre-fix, pinned Grove histories. |
| [src/settle/compute/grove_paxos_capital.py](../src/settle/compute/grove_paxos_capital.py) | Reviewed Paxos boundary receipts for pinned Grove capital histories. |
| [src/settle/compute/grove_plume_capital.py](../src/settle/compute/grove_plume_capital.py) | Reviewed October 2025 JTRSY bridge/redemption route funding Plume Apollo. |
| [src/settle/compute/grove_rlusd_conversions.py](../src/settle/compute/grove_rlusd_conversions.py) | Reviewed RLUSD/USDC issuer settlements at Grove's Ripple entrypoint. |
| [src/settle/compute/grove_secondary_cash.py](../src/settle/compute/grove_secondary_cash.py) | Expose observed Grove cash omitted from the revenue venue list in allocation-only financing diagnostics. |
| [src/settle/compute/grove_stac_capital.py](../src/settle/compute/grove_stac_capital.py) | Link Grove's first STAC subscriptions to their delayed share issuances. |
| [src/settle/compute/spark_arbitrum_spells.py](../src/settle/compute/spark_arbitrum_spells.py) | Authenticate two governance draws sent through the Spark subproxy to L2s. |
| [src/settle/compute/spark_b2c2_capital.py](../src/settle/compute/spark_b2c2_capital.py) | Spark's spell-authorized B2C2 wallet as a capital boundary. |
| [src/settle/compute/spark_base_withdrawals.py](../src/settle/compute/spark_base_withdrawals.py) | Carry authenticated June/July 2026 Base withdrawals through native custody. |
| [src/settle/compute/spark_binance_capital.py](../src/settle/compute/spark_binance_capital.py) | Spark's two August 12 Binance OTC tests, stopping at the approved boundary. |
| [src/settle/compute/spark_buidl_redemptions.py](../src/settle/compute/spark_buidl_redemptions.py) | Two reviewed Spark BUIDL exits paid directly by the known issuer payer. |
| [src/settle/compute/spark_buidl_subscriptions.py](../src/settle/compute/spark_buidl_subscriptions.py) | Spark's seven initial BUIDL subscriptions, linked at the issuer boundary. |
| [src/settle/compute/spark_early_base_seed.py](../src/settle/compute/spark_early_base_seed.py) | Three early spell draws sent through the subproxy to Base (198m USDS). |
| [src/settle/compute/spark_june_op_uni_routes.py](../src/settle/compute/spark_june_op_uni_routes.py) | June 22 partial native withdrawals: two returned, two still pending. |
| [src/settle/compute/spark_native_seed.py](../src/settle/compute/spark_native_seed.py) | Identify the spell's 400m Sky-funded Optimism/Unichain seed allocation. |
| [src/settle/compute/spark_op_uni_withdrawals.py](../src/settle/compute/spark_op_uni_withdrawals.py) | July 6 Optimism/Unichain withdrawals, finalized on Ethereum July 13. |
| [src/settle/compute/spark_par_swap_gains.py](../src/settle/compute/spark_par_swap_gains.py) | Proven historical Spark swap earnings, without new borrowed principal. |
| [src/settle/compute/spark_paxos_capital.py](../src/settle/compute/spark_paxos_capital.py) | Reviewed Spark PYUSD→USDC conversion boundary; no payer-wide income rule. |
| [src/settle/compute/spark_reserve_gifts.py](../src/settle/compute/spark_reserve_gifts.py) | Recognize observed reserve-factor gifts in older Spark capital snapshots. |
| [src/settle/compute/spark_separate_savings_routes.py](../src/settle/compute/spark_separate_savings_routes.py) | Keep the independently witnessed May 2 Sky and saver token routes separate. |
| [src/settle/compute/spark_subproxy_reserve_gifts.py](../src/settle/compute/spark_subproxy_reserve_gifts.py) | Recognize earned reserve balances forwarded through Spark's SubProxy. |
| [src/settle/compute/spark_uscc_capital.py](../src/settle/compute/spark_uscc_capital.py) | USCC subscription custody and delayed, reviewed cash settlement groups. |
| [src/settle/compute/spark_ustb_capital.py](../src/settle/compute/spark_ustb_capital.py) | USTB historical capital: actual subscription NAV and reviewed cash exits. |
| [src/settle/normalize/allocation_async_vaults.py](../src/settle/normalize/allocation_async_vaults.py) | ERC-7540 beneficial ownership through subscriptions and redemptions. |
| [src/settle/normalize/allocation_basin.py](../src/settle/normalize/allocation_basin.py) | Track Grove's internal Basin shares at the PAU's actual cash boundaries. |
| [src/settle/normalize/allocation_bridges.py](../src/settle/normalize/allocation_bridges.py) | Match CCTP v1 burns and mints by authenticated message identity. |
| [src/settle/normalize/allocation_capital.py](../src/settle/normalize/allocation_capital.py) | Inception-to-pin ALM capital movements from raw on-chain events. |
| [src/settle/normalize/allocation_curve_swaps.py](../src/settle/normalize/allocation_curve_swaps.py) | Actual stablecoin swap gains are earned funding, not unknown loan receipts. |
| [src/settle/normalize/allocation_custody.py](../src/settle/normalize/allocation_custody.py) | Verified cash-funded facilities and pending BUIDL claims, for tracing only. |
| [src/settle/normalize/allocation_eoa.py](../src/settle/normalize/allocation_eoa.py) | Allocation entry/exit boundaries for configured, commingled EOA venues. |
| [src/settle/normalize/allocation_ethena.py](../src/settle/normalize/allocation_ethena.py) | Preserve borrowed principal while sUSDe withdrawals await cooldown. |
| [src/settle/normalize/allocation_merkl.py](../src/settle/normalize/allocation_merkl.py) | Recognize Merkl wrapper rewards in capital movements using receipt events. |
| [src/settle/normalize/allocation_morpho_fees.py](../src/settle/normalize/allocation_morpho_fees.py) | Identify performance-fee shares separately from funded ERC4626 deposits. |
| [src/settle/normalize/allocation_nfts.py](../src/settle/normalize/allocation_nfts.py) | NFT liquidity funding, including principal awaiting V3 Collect. |
| [src/settle/normalize/allocation_paxos.py](../src/settle/normalize/allocation_paxos.py) | Grove's authorized Paxos USDC bridge entrypoint as a financing boundary. |
| [src/settle/normalize/allocation_principal_returns.py](../src/settle/normalize/allocation_principal_returns.py) | Apply configured day-net principal exceptions to capital-history receipts. |
| [src/settle/normalize/allocation_psm.py](../src/settle/normalize/allocation_psm.py) | Preserve borrowed basis while an ALM holds non-transferable PSM3 shares. |
| [src/settle/normalize/allocation_superstate.py](../src/settle/normalize/allocation_superstate.py) | Capital-only Superstate pricing, using the appropriate fund oracle. |
| [src/settle/normalize/allocation_vault_deposits.py](../src/settle/normalize/allocation_vault_deposits.py) | Price actual ERC-4626 deposits without dropping other incoming shares. |

### Replay inputs, tests and evidence that must remain linked

- Keep each retained module’s regression tests and its referenced receipt fixtures. Do not delete them merely because routine production no longer enables the full funding replay.
- [Spark Anchorage normalized principal history](../tests/fixtures/spark_anchorage_origin_history.jsonl.gz)
- [Spark spUSDS normalized history](../tests/fixtures/spark_spusds_origin_history.jsonl.gz)
- [Base Morpho normalized history](../tests/fixtures/spark_base_morpho_origin_history.jsonl.gz)
- [USDC graph supplement](../tests/fixtures/spark_usdc_origin_graph_supplement.jsonl.gz)
- [Authenticated CCTP draw receipts](../tests/fixtures/spark_usdc_cctp_draw_receipts.json.gz)
- [Anchorage funding evidence used for its daily loan denominator](../reconciliation/spark_anchorage_receipt_funding_origins_2026_08.json.gz)
- [Month-end funding-origin inventory](../reconciliation/funding_origin_monthly_2026_01_08.json)
- [Latest monthly capital and dependency snapshot](../reconciliation/allocation_capital_readiness_2026_08.json.gz)

Historical result files retain their original source hashes and conclusions. A historical “fully reconciled” numerical model is not a new acceptance requirement or a claim that all Savings attribution is resolved.
