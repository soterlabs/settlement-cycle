# Reproducing the Sky/Grove rebate split

Research date: 12 November 2026. USD amounts; rebates are settled in USDC.

This recipe reproduces the provisional JAAA/JTRSY allocation discussed with Grove. It does not authorize a settlement adjustment. The rebate rates and fees come from supplied documents; balances and NAVs come from historical on-chain observations. Ownership assumptions are stated separately.

## Historical note and instructions for reproducing the agreed working table

**For this recipe, JTRSY held by Spark is treated economically like an SDE: its rebate income is attributed to Sky.** This is a working economic allocation, not a claim that JTRSY had already received the later formal Atlas SDE designation. Apply it to the entire 2025 JTRSY rebate, including Spark's custody period, and to January–July 2026. Grove receiving the payment does not give Grove economic ownership of the JTRSY rebate.

The evidence and its limits are:

- The [February 2025 Stability Scope proposal, ALLOCATOR-SPARK-A section](https://forum.skyeco.com/t/feb-20-2025-stability-scope-parameter-changes-22/26003), posted 17 February for the 20 February executive, explicitly intended Spark's allocator Stability Fee to approximately track its allocation-system APY so profits went to Sky until the Spark Star launch. This is direct evidence of the pre-launch profit-attribution policy, not an exact per-venue reconciliation.
- The [April 2025 SP-BEAM explanation](https://forum.skyeco.com/t/sp-beam-configuration/26300) describes adjusting allocator fees to reflect actual generated revenue and offset idle sUSDS effects.
- The [MSC #1 explanation](https://forum.skyeco.com/t/monthly-settlement-cycle-1-july-august-september-18-2025-spell/27173) confirms earlier aggregate revenue accrual through Spark's Stability Fee, followed by reconciliation and payment of the Prime's entitlement. Collection at the ilk level alone does not prove that every venue's income belonged exclusively to Sky.
- The July/August MSC sheets linked from that post show JTRSY Prime revenue of zero: Spark July Sky revenue **$1,101,097.788**, Grove July **$91,161.70533**, and Grove August **$1,213,602.559**. These establish the reported full allocation to Sky for those months.
- A colleague familiar with the historical calculations confirmed the full-yield-pass-through interpretation. Applying it to all 2025 JTRSY management-fee rebates is the working assumption adopted here; the supplied evidence does not include an independently verified April–June venue-level settlement or an explicit rebate-entitlement agreement.
- Do not use a supposed July SPK token launch as an accounting boundary: [Spark's white paper, F.9](https://docs.spark.finance/mica/4) gives **17 June 2025** for the public offer/admission to trading. The exact accounting transition date and application to management-fee rebates remain confirmation items, not automatic reasons to change this working table.

**To reproduce the latest table, use Method B only:** preserve each invoiced net rebate and allocate it using on-chain gross-rebate weights. Method A is a separate diagnostic estimate and must not be substituted. Do not reintroduce a 30 October or 20 November JTRSY ownership cutoff, a Spark recipient, or a separate $15,625 transition allocation. The November cutoff below applies to the assumed JAAA Sky slice only.

The exact calculation inputs sufficient to reproduce the table without the local scripts are:

| Fund/year | Total on-chain gross rebate (denominator) | Sky gross rebate (numerator) | Invoiced net rebate |
|---|---:|---|---:|
| JAAA 2025 | 527734.1816750564005688932600 | `325000000 × 0.002 × 42 / 365` | 413320 |
| JAAA 2026 through July | 583106.1203638834778943133714 | `325000000 × 0.002 × 67 / 365` | 573276 |
| JTRSY 2025 | 504339.1483646454966582161366 | Equal to denominator: 100% Sky | 356999 |
| JTRSY 2026 through July | 727975.4631198996548356111370 | Equal to denominator: 100% Sky | 713408 |

For each row, calculate **Sky = invoiced net × numerator / denominator**, then **Grove = invoiced net − Sky**. Do not subtract Coinbase fees again: they are already deducted in the invoiced net amounts. Retain decimal precision and round monetary displays to cents only after calculation; aggregate unrounded amounts for year and grand totals.

| Position | Year | Sky | Grove |
|---|---|---:|---:|
| JAAA | 2025 | $58,578.87 | $354,741.13 |
| JAAA | 2026 through July | $117,303.63 | $455,972.37 |
| JTRSY | 2025 | $356,999.00 | $0.00 |
| JTRSY | 2026 through July | $713,408.00 | $0.00 |
| **Total** | | **$1,246,289.50** | **$810,713.50** |

| Year | Sky | Grove |
|---|---:|---:|
| 2025 | $415,577.87 | $354,741.13 |
| 2026 through July | $830,711.63 | $455,972.37 |
| **Total** | **$1,246,289.50** | **$810,713.50** |

Control total: **$2,057,003.00** across both recipients. These instructions supersede the older allocation outputs described in Section 10; they do not eliminate the JAAA cap/date assumptions or the confirmation items in Section 9.

## 1. Inputs and evidence

The four rebate PDFs in this directory are identified by invoice number:

| Invoice | Fund | Service period | Gross rebate, rounded | Coinbase deduction | Net rebate |
|---|---|---|---:|---:|---:|
| AGL-ANM-2025-01 | JAAA | 24 July–31 December 2025 | 530,861 | 117,541 | 413,320 |
| AGL-ANM-2026-01 | JAAA | 1 January–31 July 2026 | 584,818 | 11,542 | 573,276 |
| TAS-ANM-2025-01 | JTRSY | 5 April–31 December 2025 | 507,884 | 150,885 | 356,999 |
| TAS-ANM-2026-01 | JTRSY | 1 January–31 July 2026 | 728,223 | 14,815 | 713,408 |

`Grove Distribution Fee Reconciliation 2.zip` contains the workbook `Grove Distribution Fee 2025 and 2026 YTD.xlsx` and six Coinbase invoices. Ignore `__MACOSX` entries when extracting.

The workbook has `JAAA Monthly`, `JTRSY Monthly`, `JAAA`, and `JTRSY` sheets. The monthly sheets calculate management fees from raw column W, filtering by column D (the entry's **To Date**), and multiply by rebate bps / management-fee bps. The workbook's unrounded gross rebates are:

| Fund | 2025 | January–July 2026 |
|---|---:|---:|
| JAAA | 530860.96084 | 584817.66109 |
| JTRSY | 507883.714731 | 728223.09181 |

The documented deductions allocate 95% of Coinbase fees between the funds using aggregate subscription/redemption conversion volumes: approximately 43.79% JAAA and 56.21% JTRSY. This fund-level allocation does **not** establish the Sky/Grove split.

## 2. Chain and contract addresses

Use archive-capable RPC endpoints for Ethereum (chain ID 1) and Avalanche C-Chain (chain ID 43114). Do not use current balances or current NAVs for historical dates.

### Token contracts

| Fund/venue | Chain | Token | Decimals |
|---|---|---|---:|
| JAAA / Grove E8 | Ethereum | `0x5a0f93d040de44e78f251b03c43be9cf317dcf64` | 6 |
| JAAA / Grove E20 | Avalanche C-Chain | `0x58f93d6b1ef2f44ec379cb975657c132cbed3b6b` | 6 |
| JTRSY / Grove E9 and earlier Spark holding | Ethereum | `0x8c213ee79581ff4984583c6a801e5263418c4b86` | 6 |

### Holders included in this reconstruction

| Holder | Chain | Address | Included exposure |
|---|---|---|---|
| Grove ALM Proxy | Ethereum | `0x491edfb0b8b608044e227225c715981a30f3a44e` | JAAA and JTRSY |
| Grove ALM Proxy | Avalanche C-Chain | `0x7107dd8f56642327945294a18a4280c78e153644` | JAAA |
| Spark ALM Proxy | Ethereum | `0x1601843c5e9bc251a3272907010afa41fa18347e` | JTRSY before its transfer to Grove |

These are ALM holders, not SubProxies. Token addresses and Grove holder addresses are recorded in `config/grove.yaml`; Spark's holder is in `config/spark.yaml`.

### Historical NAV sources

All NAV calls below are on **Ethereum**, including the NAV used for Avalanche JAAA.

| Fund | Historical dates used | Contract |
|---|---|---|
| JAAA | 24–27 July 2025 | Legacy vault `0xe9d1f733f406d4bbbdfac6d4cfcd2e13a6ee1d01` |
| JAAA | 28 July 2025–31 July 2026 | `0x4880799ee5200fc58da299e965df644fbf46780b` |
| JTRSY | 7 April–27 July 2025 | Legacy vault `0x36036ffd9b1c6966ab23209e073c68eb9a992f50` |
| JTRSY | 28 July 2025–31 July 2026 | `0xfe6920eb6c421f1179ca8c8d4170530cdbdfd77a` |

These date ranges specify the sources used in this reconstruction, not independently established contract deployment dates.

## 3. Reading historical data

1. For each UTC calendar day, find the last block whose timestamp is at most `23:59:59 UTC`; resolve Ethereum and Avalanche block numbers independently.
2. Record the date, chain, block number, block hash, and timestamp so subsequent calls use the same historical snapshot.
3. At that block, call `balanceOf(holder)` on each relevant token contract and divide the returned integer by `10**6` to obtain token shares.
4. On Ethereum at the same day's Ethereum block, call `convertToAssets(10**18)` on the applicable NAV contract and divide the returned integer by `10**18` to obtain the USD price per token used here.
5. Calculate daily value as `token shares × historical NAV`; use Ethereum's JAAA NAV for both Ethereum and Avalanche JAAA.
6. Treat missing, reverting, empty, or zero NAV responses as missing evidence requiring investigation; never silently substitute par or today's NAV for a material holding.

Alternatively, reconstruct balances from ERC-20 `Transfer` logs, including mints/burns, with a verified opening balance. Apply all transfers through the EOD block, count self-transfers net zero, and verify checkpoints against historical `balanceOf`. The saved Grove series used retained indexer transfer balances; the earlier Spark series used fetched Transfer logs. NAVs were historical RPC reads.

Do not infer the opening balance is zero merely because the selected reporting window begins on that date.

## 4. Date windows and custody handover

1. JAAA 2025: sum Grove Ethereum plus Avalanche holdings from **24 July through 31 December**, inclusive, matching the invoice service window; exclude earlier test holdings (about $11.51 of modeled rebate).
2. JAAA 2026: sum both Grove holdings from **1 January through 31 July**, inclusive.
3. JTRSY 2025: use Spark's Ethereum holding from **7 April through 27 July**, then Grove's Ethereum holding from **28 July through 31 December**, inclusive.
4. The JTRSY invoice starts **5 April**, but the saved on-chain token series starts **7 April**; do not invent token balances for the two earlier days. Earlier off-chain subscription entitlement is a coverage difference to investigate.
5. The custody handover is the [28 July 2025 transfer](https://etherscan.io/tx/0xdd5bf338720c06dd098216e53a276a8bac00fddd80e94757f4e12c9e09f853ab); do not count both holders for the same transferred position on that day's EOD snapshot.
6. JTRSY 2026: use Grove's Ethereum holding from **1 January through 31 July**, inclusive.

The baseline excludes pending/unclaimed shares, redemption custody outside these wallets, and temporary Plume JTRSY custody. This reproduces the existing wallet-based estimate; it is not a claim of complete economic fee entitlement.

## 5. Documented rebate rates

Apply simple annual rates using ACT/365; do not compound or use investment yield as the rebate rate.

| Fund | Dates, inclusive | Rebate APR |
|---|---|---:|
| JTRSY | Through 22 April 2025 | 0.15% |
| JTRSY | 23 April–30 September 2025 | 0.25% |
| JTRSY | 1 October 2025–31 July 2026 | 0.15% |
| JAAA | 24 July–30 October 2025 | 0.10% |
| JAAA | 31 October 2025–31 July 2026 | 0.20% |

For each fund/year, calculate:

```text
daily_gross_rebate = EOD_value_USD × rebate_APR / 365
onchain_gross_rebate = sum(daily_gross_rebate)
```

## 6. Provisional ownership rules

These are allocation assumptions, not results of `balanceOf` or statements proven by the rebate invoices.

1. JAAA exclusive Sky entitlement starts **20 November 2025**, following the [Atlas revenue-sharing amendment](https://github.com/sky-ecosystem/next-gen-atlas/commit/7b43d159e098b30e67c4be6a7594a237a340fa58); designation as SDE on 23 October is not itself the same revenue-sharing rule.
2. Assume Sky exposure is `min(Ethereum JAAA value, $325m)` from **20 November 2025 through 8 March 2026**, inclusive; Avalanche JAAA remains outside this assumed Sky slice.
3. The $325m cap throughout late 2025 remains unconfirmed; the PDFs and workbook do not establish it.
4. The 8 March cutoff follows the previously identified March redemption boundary; it is an explicit assumption here, not a claim that the off-chain capital account stopped accruing on that same day.
5. This produces JAAA Sky gross rebate of `$325m × 0.002 × 42/365 = $74,794.5205479452` in 2025 and `$325m × 0.002 × 67/365 = $119,315.068493151` in 2026.
6. Provisionally allocate remaining JAAA rebate entitlement to Grove, including the period before 20 November 2025; historical settlement interactions are not independently resolved by this assumption.
7. Provisionally allocate **all JTRSY rebate income in 2025 to Sky**, including the earlier Spark custody period, based on the reported historical MSC treatment: all investment yield passed to Sky and the Prime did not bear the shortfall against the Base Rate.
8. The supplied July/August MSC sheets show zero Prime revenue and positive Sky revenue for JTRSY, supporting that account but not independently proving entitlement to management-fee rebates.
9. Confirm that the historical full-yield-pass-through treatment includes management-fee rebates; this replaces the earlier assumed 30 October cutoff and separate transition-period allocation.
10. All modeled JTRSY exposure during January–July 2026 is also allocated to Sky.

## 7. Choose which question the calculation answers

Do not mix these two methods: they have different denominators and different total net amounts.

### A. Independently estimate the rebate and deduct the invoiced fees

```text
estimated_net = onchain_gross_rebate − invoiced_fee_deduction
Sky_net = Sky_onchain_gross / onchain_gross_rebate × estimated_net
Grove_net = estimated_net − Sky_net
```

This is the literal replacement of the workbook gross amounts while keeping the documented deductions fixed. Its total is allowed to differ from the invoices.

### B. Allocate the actual invoiced net rebates using on-chain weights

```text
Sky_net = Sky_onchain_gross / onchain_gross_rebate × invoiced_net_rebate
Grove_net = invoiced_net_rebate − Sky_net
```

This is the recommended provisional method for splitting the actual rebates: it preserves the documented net amounts and uses the independent on-chain calculation to estimate ownership weights. It does not independently prove those invoice amounts. Proportional allocation of the difference/fees between Sky and Grove remains an assumption.

Use Decimal arithmetic, retain full precision throughout, and round only after aggregating each displayed result.

## 8. Expected results

The following use the saved gross-rebate inputs with the revised full-year JTRSY ownership assumption; fresh corrected custody coverage may change them.

| Fund | Year | On-chain gross | Sky gross | Invoice deduction | Invoiced net |
|---|---|---:|---:|---:|---:|
| JAAA | 2025 | 527734.1816750564 | 74794.5205479452 | 117541 | 413320 |
| JAAA | 2026 through July | 583106.1203638835 | 119315.0684931507 | 11542 | 573276 |
| JTRSY | 2025 | 504339.1483646455 | 504339.1483646455 | 150885 | 356999 |
| JTRSY | 2026 through July | 727975.4631198997 | 727975.4631198997 | 14815 | 713408 |

### Method A: estimated net rebate

| Year | Sky | Grove |
|---|---:|---:|
| 2025 | 411589.86 | 352057.47 |
| 2026 through July | 830113.81 | 454610.77 |
| Total | 1241703.67 | 806668.24 |

This estimates approximately $8,631 less total net rebate than the invoices; the missing coverage and differing accrual dates are not proof that the invoices are wrong.

### Method B: split of actual invoiced net rebates

| Position | Year | Sky | Grove |
|---|---|---:|---:|
| JAAA | 2025 | 58578.87 | 354741.13 |
| JTRSY | 2025 | 356999.00 | 0.00 |
| JAAA | 2026 through July | 117303.63 | 455972.37 |
| JTRSY | 2026 through July | 713408.00 | 0.00 |

| Year | Sky | Grove |
|---|---:|---:|
| 2025 | 415577.87 | 354741.13 |
| 2026 through July | 830711.63 | 455972.37 |
| Total | 1246289.50 | 810713.50 |

The earlier on-chain-weighted Method B table, using the now-superseded 30 October JTRSY cutoff, was **Sky $936,584.85 / Grove $1,120,418.15**. The revised full-year JTRSY allocation shifts **$309,704.65 from Grove to Sky**, preserving the total rebate. JAAA calculations are unchanged.

For comparison, the original workbook-weighted table with the old ownership assumptions was **Sky $935,575.99 / Grove $1,121,427.01**. Relative to that table, the combined changes to data weights and JTRSY ownership shift approximately **$310,713.51 from Grove to Sky**. Method A instead estimates a different total net rebate and must not be substituted for Method B without stating that distinction.

## 9. Checks and unresolved items

- Verify each day's holdings are valued with that day's NAV and the correct chain-specific EOD block.
- Verify Spark/Grove handover continuity and prevent double-counting shares during custody transfers.
- Verify that only Ethereum JAAA enters the assumed $325m Sky slice.
- Verify that each fund/year's Sky and Grove allocations sum to the selected net amount (estimated net for A, invoiced net for B).
- Confirm that the reported historical full-yield-pass-through treatment of JTRSY includes management-fee rebates; the revised full-year Sky allocation remains provisional pending that confirmation.
- Investigate pending/claimable tokens, off-chain subscription dates, and temporary Plume custody before treating the wallet estimate as complete.
- The workbook excludes the entire 31 July–2 August 2026 fee entry because it filters by To Date; a one-day prorating suggests about $2,143 JAAA and $3,517 JTRSY potentially excluded from a period described as through 31 July. This is an investigation item, not an authorized adjustment.
- The documents still do not explain Grove's proposed $70,378.80 JAAA Sky share for 2026.
- Do not treat zero Prime revenue in the old July/August MSC sheets as proof of exclusive Sky ownership of subsequent rebates.

## 10. Saved local evidence and reproduction

The original evidence bundle is outside Git at `/data/grove-rebate-audit/`:

- `audit.json`: daily Grove token shares/NAV/values and earlier Spark JTRSY values.
- `nav.jsonl` and `legacy_nav.jsonl`: historical NAV RPC results with dates and block numbers.
- `spark_jtrsy_transfers.json`: earlier Spark transfer observations.
- `sources.json`: paths and SHA-256 hashes of retained indexer fixtures and other evidence.
- `new_documents/Grove Distribution Fee 2025 and 2026 YTD.xlsx`: extracted workbook.
- `new_documents/rebate_split_analysis.json`: workbook-based calculation.
- `onchain_split.py`: historical calculation artifact using the superseded 30 October JTRSY cutoff; it has not been updated for this document revision.
- `onchain_split.json`: full-precision historical results using that superseded cutoff; JAAA and total gross/net inputs remain reusable, but its JTRSY ownership split and aggregate totals are superseded by Section 8.

On another machine, copy the evidence bundle or regenerate the daily inputs using Sections 2–5. Do not assume `/data` artifacts travel with a Git checkout.

## Executable October settlement integration

Run `python scripts/calculate_october_partner_rebate.py` from the repository root.
It uses the committed `partner-rebate-daily.json` snapshots and
`partner-rebate-inputs.json` invoice nets/receipts; no local `/data` dependency or
RPC is needed. The saved daily observations are research evidence, not a fresh
on-chain fetch. Full-precision daily EOD values reproduce Method B above.

The executable applies all JTRSY rebates to Sky, including Spark custody, under
the historical treatment described above. The provisional JAAA cap, date
boundaries and proportional fee allocation remain explicit assumptions. All six
USDC receipts are included once, as **partner rebate** in October MSC adjustments;
ordinary October E8/E9 investment income and prior-month reports are unchanged.

Expected Sky/Grove: JAAA 2025 58,578.87/354,741.13; JAAA 2026
117,303.63/455,972.37; JTRSY 2025 356,999/0; JTRSY 2026 713,408/0.
Total: **1,246,289.50 / 810,713.50 USDS**. Fees are already in the invoice nets.
The former PR allocation (1,141,145.80/915,857.20) is replaced, not added again.
`replacements()` combines the independently retained Jan–September adjustments
with this split. It is idempotent and leaves that historical ledger untouched.
