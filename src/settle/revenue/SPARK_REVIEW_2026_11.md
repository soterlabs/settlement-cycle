# Spark review — 9 November 2026

Scope: reporting only. MSC calculations, settlement inputs and old immutable
report selections keep their existing recognition. The audits below are read-only review previews. Deployment/backfill appends
new revisions; publication status must be verified in the live API, not inferred
from these static tables.

## Decisions and evidence

| Review | Finding | Treatment |
|---|---|---|
| 1. SparkLend non-USD reserves | Gross reserve price changes include Savings-funded ETH backing. Removing all reserve gains would also discard Spark's own treasury gains. | Hide the old row. Net matched ALM ETH/depositor principal FX, retain residual Spark-owned ETH gains under ETH/WETH and genuine Ops reserve gains under Other treasury reserve gains. Preserve gross audit metrics. |
| 2. Savings ETH revaluation | Principal FX is distinct from interest earned by depositors. Existing source already separates daily translated VSR interest and principal FX. | Cost of capital includes the USD value of depositor interest only. The matched principal FX comes out of both asset gains and funding. This changes the gross presentation, not net revenue. |
| 3. Unclassified SparkLend receipts | An unattributed source is not independently established additional income. | Hide its statement row, preserve the original source metric and existing exclusions. Do not replace unknowns with zero. |
| 4. Base curator fees | Fee mints are genuine, but most compensate fees charged to Spark's own supply. Gross receipts are not all fees from external clients. | Split at pre-mint controlled share ownership: external portion under Curator fees, self portion as rebate to the vault's net supply revenue. Include pending entitlement changes. No change in aggregate revenue. Attribution remains provisional. |
| 5. Dust income | Small supported amounts are still revenue. | Hide Other reviewed income and descendants in statement presentation, retain amounts in allocation revenue/API audit rows. |
| 6. Contributor SPK | Claiming vested tokens is not the expense recognition trigger. User-supplied grant method is not yet in the expense workbook. | Provisional 1.2bn × 0.065117 / 48 = 1,627,925 USD each full month, June 2025–May 2029; 2026 only here. Prorate MTD by calendar days, future months blank. Under Compensation and benefits and the matching noncash cash-flow add-back. Never use 388m claimed tokens as expense timing. |
| 7. Cash-flow ETH | Removing one side alone would create a cash-flow distortion. | Gains reversal and depositor noncash add-back use the revised income metrics; matched principal FX disappears from both. Actual cash is unchanged. |
| 8. Two Safes | Existing rules treated these counterparties as unclassified. | Expand the cash cluster. Own-wallet transfers become internal; external payments default to provisional expenses, with more specific counterparty rules taking precedence. External receipts stay unresolved unless positively classified. |
| 9. Investing layout | Caption parentage creates an inappropriate section dropdown. | Sky direct and treasury proceeds rows have no layout parent, like the other investing rows. |
| 10. Sky liquidity cash | Raw USDT transfers through an SDE pool include swaps. | Reuse authenticated liquidity principal events/receipts; only subscriptions/redemptions move to Sky direct. Paired reclassification leaves total investing cash unchanged. Effective designation dates remain enforced. |

October DR remains TBD until its approved monthly source is published. The
existing dashboard already limits comparable YTD revenue and expenses to the
last supplied expense month (October), while retaining live November monthly
cells. Expense presentation already has the requested two levels. Neither needs
a duplicate correction.

## Curator-fee method

For each authenticated performance-fee mint:

- Gross fee value = minted fee shares valued at that event's vault NAV.
- Self rebate = gross value × controlled shares / total shares, immediately
  **before** this fee mint. V1 emits after minting; V2 emits before its mints.
- External fee = gross fee minus self rebate.
- Accrual adds closing unminted entitlement and subtracts opening entitlement,
  splitting each entitlement by the boundary controlled-share fraction.
- Verify all vault supply mints/burns and controlled share transfers reproduce
  closing onchain totalSupply and balanceOf. Duplicate/conflicting logs fail.

Scope is the configured Spark-controlled wallets for the vault's chain.
Ownership is economic scope, not a claim that each vault depositor is unrelated.
Rebates are additional to net supplier NAV yield, not an extra independent fee
stream. Their sum with external fees equals the original gross fee. Missing
attribution retains gross known revenue in an explicit unallocated audit bucket;
it does not advertise gross receipts as external fees.

October Base: 15,870 fee events; gross accrual 171,259.11 USD, external fees
2,135.35 USD. All six configured curator vaults: **2,328.10 USD external**.
Spark's public historical financials show **2,338.11 USD** for October. The
10.01 USD difference remains unexplained (timing/scope/rounding are hypotheses,
not established causes). Do not calibrate or plug to the public dashboard.

Sources: [Spark financials](https://data.spark.finance/financials),
[monthly returns](https://data.spark.finance/api/v1/financials/returns/history?window=1y).

## Expanded cash perimeter

| Wallet | Inclusion evidence | Qualification |
|---|---|---|
| `0x8a3c66b30e2bbe5c2b1cff1054d869171fb87766` | Reviewer identifies DevCo; all three signers overlap the existing DevCo Safe. | Onchain threshold 2 of 3. |
| `0xdfbc82d27aa60e9f55bdfe043a881c18520b71f1` | Reviewer identifies Foundation; two signers overlap its existing Safe. | Onchain threshold **1 of 3**, not 2 of 3. Signer overlap alone does not establish exclusive legal control. |

Jan–Oct read-only balance/transfer audit: 33 cash-token events; every monthly
balance movement matches the observed transfer movement exactly. Closing cash
in these wallets on October 31: **1,062,964.39 USD**. Transfers from previously
configured cluster wallets: **1,760,000 USD**, not all receipts into these Safes.
Other incoming transfers exist and remain subject to classification. External
payments before specific counterparty treatment total **1,509,660 USD**.
Adding a wallet scope can increase, rather than automatically close, a cash gap.

## Audited liquidity-only Sky cash

Positive means cash returned; negative means deployed. Small Curve withdrawals
exist even in months with an approximately constant 50m balance; January is not
forced to zero. These are local retained-ledger previews, not new publications.

| 2026 month | Old Sky cash, USD | Liquidity-only Sky cash, USD |
|---|---:|---:|
| 2026-01 | -84,998,232.34 | 23,872.54 |
| 2026-02 | -98,870,298.61 | 23,057.54 |
| 2026-03 | 8,588,990.86 | 32,325.16 |
| 2026-04 | 143,179,021.07 | 22,978.80 |
| 2026-05 | -247,987,954.86 | 1,428,945.11 |
| 2026-06 | 11,478,821.45 | -3,430,684.50 |
| 2026-07 | 110,182,133.81 | 11,355.12 |
| 2026-08 | -13,428,678.00 | 3,234.90 |
| 2026-09 | -57,842,311.39 | 4,553.73 |
| 2026-10 | 27,216,152.75 | 2,841,089.04 |
| Jan–Oct | -202,482,355.27 | 960,727.43 |

All ten monthly investing totals are unchanged. The corrected total is about
+0.96m, not exactly zero; no balancing adjustment is made.

## Publication and rollout

1. Merge/deploy the backend first. Default legacy income/cash reads remain on
   v3.10/events.v1. Existing pinned selections keep their archived inputs.
2. If the latest completed-day source row is missing, first collect it with
   `SPARK_REPORTING_REVIEW_ENABLED=1 python -m settle.revenue.spark_reporting_job --from <cutoff> --to <cutoff>`.
   Prepare replacement reporting sources with
   `python -m settle.revenue.spark_reporting_job --from 2026-01-31 --to <last-completed-UTC-day> --review-inputs`.
   This appends reviewed curator attribution/SDE principal at retained pins;
   failures remain explicit. It preserves other saved income fields and MSC.
   Environment credentials must be injected privately; never include them in
   CLI arguments, logs or documentation.
3. Publish cash replacements using
   `python -m settle.revenue.cash_flow.job --from 2026-01-31 --to <last-completed-UTC-day> --review`.
   First use `--dry-run --output-dir <private-preview-directory>` to inspect
   cash scope, missing inputs, expenses and conservation. Fresh scope needs a
   fresh capture; the capture configuration revision prevents old-scope reuse.
4. Validate the two reviewed endpoints before deploying the dashboard:
   `/v3/reporting/statements?prime=spark&year=2026&accounting=true&review=true`
   and `/v3/reporting/cash-flow?prime=spark&year=2026&review=true`.
   The dashboard PR opts into these policies. Deploy it only after reviewed
   historical/MTD sources and cash publications exist. Missing reviewed months
   deliberately stay TBD rather than mixing old wallet scope into new months.
5. For future scheduled reporting workers, set
   `SPARK_REPORTING_REVIEW_ENABLED=1` in both the source and cash worker
   environments. Normal source collection then includes curator attribution
   and SDE witnesses, and the cash worker uses the review policy. This flag
   does not authorize publishing during this review task.

`--review-inputs` is a publishing command, not the read-only audit performed in
this task. No settlement replay is required. Preparing this change did not publish corrected reports. The authorized rollout
subsequently runs source and cash publication jobs; retained old selections stay
unchanged.

When the expense sheet arrives, set its `includes_contributor_compensation`
flag only after confirming Compensation and benefits already contains this
same grant. The dashboard then suppresses the extra leaf, and the cash bridge
removes SPK from payable expenses while adding it back as noncash compensation.
Archive new expense revisions; do not rewrite any old published snapshot.

Limitations retained: provisional grant inputs and fee attribution, unresolved
full-cluster receivable/noncash/transit bridges, and unidentified external Safe
receipts. These changes are not an audit certification or a guarantee of a
zero reconciliation gap.


Reviewed component reads also accept `review=true` on `/v3/reporting/fees`,
`/v3/reporting/gains` and `/v3/reporting/allocations`. Retained `revision`
selections take precedence. Per-venue `native_revenue_usd` and supply rates
remain the net vault NAV yield. `self_curation_rebate_usd` is disclosed
separately, `allocation_revenue_usd` includes it, and family totals use that
allocation revenue. The fee ledger explicitly retains gross entitlement while
`external_curator_revenue_usd` matches the external-fee statement metric. Do not
add the gross fee ledger again to the statement. Excluded reserve audit metrics
are not returned as active gains by the reviewed gains endpoint.

### Independent curator cross-check, USD

| 2026 month | External fees, this calculation | Spark public financials |
|---|---:|---:|
| 2026-01 | 30,259.29 | 30,130.79 |
| 2026-02 | 6,369.84 | 6,455.99 |
| 2026-03 | 5,356.10 | 5,310.94 |
| 2026-04 | 3,843.67 | 3,894.23 |
| 2026-05 | 3,254.82 | 3,280.51 |
| 2026-06 | 3,296.26 | 3,228.01 |
| 2026-07 | 2,944.30 | 2,921.87 |
| 2026-08 | 2,399.05 | 2,397.92 |
| 2026-09 | 2,023.91 | 2,008.15 |
| 2026-10 | 2,328.10 | 2,338.11 |
