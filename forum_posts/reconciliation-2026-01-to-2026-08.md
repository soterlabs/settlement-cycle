# Settlement Reconciliation — MSC #5–#12 (January–August 2026)

*Draft prepared by Soter Labs for the September 2026 settlement (MSC #13).*

This report reconciles three newly identified corrections to the January–August Monthly Settlement Cycles: SparkLend reserve-factor income, Grove BUIDL redemption costs, and Distribution Rewards (DR) for four previously untracked Skybase venues. It follows the [MSC #5–#9 reconciliation](https://forum.skyeco.com/t/msc-5-msc-9-reconciliation/28037) and the [MSC #5–#10 reconciliation](https://forum.skyeco.com/t/settlement-reconciliation-msc-5-10-january-june-2026/28150).

The proposed corrections add **2,569,467.85 USDS in transfers to Spark and Skybase**, and give Grove **165,013.90 USDS of relief through a reduction in debt minted**. Skybase's proposed transfer assumes no payments for these items outside the published MSC reports; any such payments must be deducted before settlement.

| Agent | Correction (USDS) | Settlement treatment |
|---|---:|---|
| Spark | +2,392,354.07 | Increase supply-side revenue; increase debt mint and transfer to Spark |
| Grove | +165,013.90 credit | Reduce Sky share and Grove debt mint; no separate transfer |
| Skybase | +177,113.78 | Increase demand-side DR and transfer to Skybase, less any off-report payments |

## Reconciliation method

This is an incremental reconciliation of the specified omissions. Earlier reconciliation amounts are excluded. The published January–August reports remain the baseline; corrections are carried into the September settlement separately from September's ordinary accrual.

Each correction traces to a dedicated calculation:

- Spark and Grove: [settlement-cycle PR #218](https://github.com/soterlabs/settlement-cycle/pull/218), using its [January–August reconciliation data](https://github.com/soterlabs/settlement-cycle/blob/536f1492089ed82fbf6eaabb956f7a194b887d47/reconciliation/2026-01_to_2026-08/data.json).
- Skybase Pendle and Morpho: calculations at [`ed08241`](https://github.com/soterlabs/settle-dr-dune/commit/ed08241beb57cebe60dcc2b278481cc2b68d795e), following [settle-dr-dune PR #20](https://github.com/soterlabs/settle-dr-dune/pull/20), with [monthly historical additions](https://github.com/soterlabs/settle-dr-dune/blob/ed08241beb57cebe60dcc2b278481cc2b68d795e/hypersync-results/skybase_historical_additions.csv).
- Skybase Grove-farm DR: [settle-dr-dune PR #21](https://github.com/soterlabs/settle-dr-dune/pull/21), supported by the [farm event and referral audit](https://github.com/soterlabs/settle-dr-dune/blob/ed08241beb57cebe60dcc2b278481cc2b68d795e/docs/grove-usds-farm.md).
- Skybase payment baseline: [published-payment audit](https://github.com/soterlabs/settle-dr-dune/blob/ed08241beb57cebe60dcc2b278481cc2b68d795e/docs/september-2026-settlement.md), against [settlement-reports at `cb3db5c`](https://github.com/soterlabs/settlement-reports/tree/cb3db5ce974f22361cff8f2a0aef1bde26aa05d7/reports/skybase).

All figures are USDS. Tables show cents; final MSC mint and transfer amounts are rounded to whole USDS after combining the adjustments with the month's settlement. Totals use unrounded calculations, so independently rounded rows can differ from a displayed total.

## Spark

SparkLend reserve treasuries swept reserve-factor income to Spark's Ethereum ALM in spTokens. The receipts entered position balances, but the accounting treated them as capital, cancelling their contribution to revenue. The correction recognizes the receipts as Spark supply-side income. This income is additional to the lending yield already measured net of the reserve factor. See the [Spark reconciliation record](https://github.com/soterlabs/settlement-cycle/blob/536f1492089ed82fbf6eaabb956f7a194b887d47/settlements/spark/2026-09/reconciliation.md).

| Month | MSC | Reserve-factor income omitted (USDS) |
|---|---|---:|
| 2026-01 | #5 | 187,229.81 |
| 2026-02 | #6 | 776,974.45 |
| 2026-03 | #7 | 192,240.53 |
| 2026-04 | #8 | 107,239.57 |
| 2026-05 | #9 | 58,633.73 |
| 2026-06 | #10 | 281,611.86 |
| 2026-07 | #11 | 317,345.18 |
| 2026-08 | #12 | 471,078.95 |
| **Total from unrounded values** | | **2,392,354.07** |

The displayed rows sum one cent higher. The settlement uses **2,392,354.07 USDS**, applied as `sv_adj`. Both debt minted and the transfer to Spark increase by that amount before final rounding, leaving Sky's net mint less transfers unchanged for this item.

Direct recognition of these treasury receipts starts September 1, 2026. The historical amount is carried once through this adjustment.

## Grove

BUIDL redemptions return approximately 99.95% of share face value. With BUIDL marked at $1, the share burn and capital outflow cancelled within the BUIDL venue, while the lower USDC receipt appeared elsewhere. The redemption cost was therefore omitted from revenue. BUIDL E10 is a fixed Sky Direct Exposure, so Sky bears that cost. See the [Grove reconciliation record](https://github.com/soterlabs/settlement-cycle/blob/536f1492089ed82fbf6eaabb956f7a194b887d47/settlements/grove/2026-09/reconciliation.md).

| Period | Component | Credit (USDS) |
|---|---|---:|
| May 2026 | Realized BUIDL redemption fees | 137,504.98 |
| August 2026 | Realized redemption fees settled before the closing boundary | 25,000.37 |
| August 31, 2026 | Excess cost of funds from omitted in-flight redemption receivable | 2,508.55 |
| **Total** | | **165,013.90** |

The in-flight correction comes from an August replay with and without the redemption receivable. It lowers Sky-side revenue by 2,508.545902 USDS, rounded to 2,508.55 USDS.

The combined correction is applied as `sky_adj: -165013.90`. It reduces debt minted against Grove's allocator by **165,013.90 USDS** before final rounding. Grove's supply-side share and transfer amount receive no separate increase.

Two amounts fall outside this historical true-up: the **321,627.21 USDS** September transition markdown on remaining BUIDL holdings, and the **12,499.85 USDS** fee on the August 31 redemption that settled September 1. The 5 bps NAV haircut takes effect September 1 and recognizes the transition markdown once in September.

## Skybase Distribution Rewards

Four venues add **177,113.78 USDS** of historical DR to Skybase's demand side:

| Venue | Attribution | Historical addition (USDS) |
|---|---|---:|
| USDS Risk Capital | Synthetic code 1999 | 1,782.89 |
| USDS Flagship | Synthetic code 1998 | 71,804.68 |
| Pendle SY-sUSDS | Synthetic code 1997 | 41,560.04 |
| Grove USDS farm | Emitted codes 0, 1 and 1002 | 61,966.17 |
| **Total** | | **177,113.78** |

The [Pendle and Morpho methodology](https://github.com/soterlabs/settle-dr-dune/blob/ed08241beb57cebe60dcc2b278481cc2b68d795e/docs/skybase-pendle-morpho.md) rewards eligible balances under the Grove XR schedule: 0.5% APY through July 8, 2026, and 0.2% APY from July 9, using daily-equivalent rates and intraday balance weighting. Pendle counts the sUSDS backing held by SY, converted to USDS; PT, YT and LP claims are not rewarded again. Morpho counts vault-wallet USDS plus the vault's proportional share of unborrowed market USDS. Borrowed assets and collateral are excluded.

The Grove farm uses its existing XR reward schedule and emitted beneficiary codes. These DR amounts belong to **Skybase**, even though the staking venue is Grove's farm. An additional **813.35 USDS** is untagged and excluded from payment. [Farm audit](https://github.com/soterlabs/settle-dr-dune/blob/ed08241beb57cebe60dcc2b278481cc2b68d795e/docs/grove-usds-farm.md).

### Monthly additions

| Month | Risk Capital | Flagship | Pendle SY | Grove farm payable | Total (USDS) |
|---|---:|---:|---:|---:|---:|
| 2026-01 | 451.61 | 0.00 | 0.84 | 0.00 | 452.45 |
| 2026-02 | 706.51 | 0.00 | 0.76 | 0.00 | 707.27 |
| 2026-03 | 374.31 | 13,788.45 | 0.85 | 0.00 | 14,163.62 |
| 2026-04 | 65.42 | 15,875.41 | 0.82 | 0.00 | 15,941.65 |
| 2026-05 | 77.12 | 17,843.96 | 3.85 | 0.00 | 17,924.93 |
| 2026-06 | 49.77 | 11,833.05 | 17,103.06 | 0.00 | 28,985.88 |
| 2026-07 | 29.93 | 6,767.26 | 11,730.41 | 32,171.02 | 50,698.62 |
| 2026-08 | 28.22 | 5,696.55 | 12,719.45 | 29,795.15 | 48,239.36 |
| **Total from unrounded values** | **1,782.89** | **71,804.68** | **41,560.04** | **61,966.17** | **177,113.78** |

The table uses the [historical additions CSV](https://github.com/soterlabs/settle-dr-dune/blob/ed08241beb57cebe60dcc2b278481cc2b68d795e/hypersync-results/skybase_historical_additions.csv) and [combined monthly DR CSV](https://github.com/soterlabs/settle-dr-dune/blob/ed08241beb57cebe60dcc2b278481cc2b68d795e/hypersync-results/dr/dr_monthly_combined.csv), filtering the latter to `USDS-GROVE` and payable codes 0, 1 and 1002.

### Published payment baseline

The January–August published Skybase workbooks contain neither codes 1997–1999 nor references to the three venue contracts. For the Grove farm, July and August published DR for the beneficiary codes matches the pipeline totals before adding the farm. These checks establish that the additions were absent from the published settlements. [Payment audit](https://github.com/soterlabs/settle-dr-dune/blob/ed08241beb57cebe60dcc2b278481cc2b68d795e/docs/september-2026-settlement.md).

The audit does not establish whether separate transfers were made outside those reports. Subject to deducting any such payments, the proposed historical DR adjustment is **177,113.78 USDS**, added to Skybase's September payment separately from September-earned DR. Skybase has no allocator debt mint.

## Proposed settlement effects

The following amounts are changes to September's ordinary settlement, before final whole-USDS rounding and any deduction for Skybase off-report payments:

| Agent | Demand-side adjustment | Supply-side adjustment | Sky-share adjustment | Debt mint change | Transfer change |
|---|---:|---:|---:|---:|---:|
| Spark | 0.00 | +2,392,354.07 | 0.00 | +2,392,354.07 | +2,392,354.07 |
| Grove | 0.00 | 0.00 | -165,013.90 | -165,013.90 | 0.00 |
| Skybase | +177,113.78 | 0.00 | 0.00 | 0.00 | +177,113.78 |
| **Total (USDS)** | **+177,113.78** | **+2,392,354.07** | **-165,013.90** | **+2,227,340.17** | **+2,569,467.85** |

The combined effect on Sky's settlement net, defined here as debt minted less transfers to agents, is **-342,127.68 USDS**. Spark's adjustment raises mint and transfer equally; Grove's credit lowers mint, and Skybase's DR raises transfers.

## Cause index

| Repository and PR or commit | Correction | Period |
|---|---|---|
| [settlement-cycle #218](https://github.com/soterlabs/settlement-cycle/pull/218) | Spark reserve-factor income; Grove realized BUIDL fees and in-flight cost of funds | January–August 2026 |
| [settle-dr-dune #20](https://github.com/soterlabs/settle-dr-dune/pull/20) | Pendle SY-sUSDS and Morpho Flagship / Risk Capital DR | January–August 2026 |
| [settle-dr-dune #21](https://github.com/soterlabs/settle-dr-dune/pull/21) | Grove-farm DR attributed to Skybase | July–August 2026 |
| [settle-dr-dune #26](https://github.com/soterlabs/settle-dr-dune/pull/26) | Audit of historical additions against published payments | January–August 2026 |
| [settle-dr-dune `ed08241`](https://github.com/soterlabs/settle-dr-dune/commit/ed08241beb57cebe60dcc2b278481cc2b68d795e) | Skybase Pendle and Morpho historical DR calculations | January–August 2026 |
