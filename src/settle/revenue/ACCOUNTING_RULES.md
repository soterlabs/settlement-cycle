# Reporting accounting rules

These are the agreed management-reporting rules for the Reporting API and its
dashboard, recorded on 2026-11-06. Apply the principles to every prime; apply
Spark-specific terms only to Spark. This is the implementation contract, not a
claim that every historical publication already complies or that these reports
are audited financial statements.

## Boundaries and sources

| Question | Rule |
|---|---|
| Settlement versus reporting | Keep independent methods and publications. MSC may use receipts; reporting may accrue the same stream. Reporting repairs must not silently rewrite MSC. |
| Venue eligibility | Settlement and reporting flags are independent. Reporting-only assets are excluded from MSC but remain visible to reporting. |
| Income versus position | Income contains revenue and expenses, never principal or closing principal-plus-yield. Derive returns using opening/closing values adjusted for capital flows. |
| Cash versus allocation | Use an explicit, versioned asset/wallet cash perimeter. A stablecoin is not automatically cash; the same token can have a different role in a savings wallet and the ALM. |
| Cash evidence | Use successful transfers, mints, burns and native transfers. Exclude failed calls and delegatecall pseudo-transfers. Internal controlled-wallet transfers and wrapping are not fresh income or expense. |
| Estimates and missing inputs | Missing is not zero. Keep TBD and source/quality metadata; do not manufacture values from a reconciliation residual. |
| History | Respect effective dates and service/earning periods. Preserve immutable source revisions and reproducible exports when replaying. |
| Cutoffs | Match the revenue and expense periods in comparative/YTD totals. For the current Spark review, Jan–Oct 2026 is the common period; October DR stays TBD until published. |

An expense-sheet snapshot is a source, not an additional expense on top of the
same item inferred from transactions. Parent and child rows must never both
contribute to a total. Maintain one approved expense source shared by the income
and cash-flow calculations, with a matching revision in the dashboard.

## Revenue and rewards

| Stream/event | Reporting treatment |
|---|---|
| Native allocation return | Accrue the prime-owned supply/vault/LP return, excluding deposits, withdrawals and rewards booked separately. |
| Allocation-linked incentives | Include in allocation revenue, separate from native yield so they are counted once. |
| Other incentives and rewards | Rewards unrelated to a specific allocation, including the agreed Foundation MORPHO and AVAX rewards, belong in Other incentives and rewards received. |
| Reward accrual | Recognize when earned where earning rules are known. Receipt settles the accrued receivable; it does not recognize the same revenue again. |
| Outstanding accrual | Cumulative earned rewards minus cumulative receipts. A negative balance is prepaid income, not an instruction to clamp to zero. |
| Reward token valuation | For nonstable rewards, establish USD value when earned/recognized and track later price changes separately. If earned before receipt, reconcile receipt against the prior accrual. |
| Sale of a reward token | Keep the original reward income. Recognize only subsequent gains/losses, without repeating earlier monthly marks. Cash proceeds enter cash flows when they meet the cash perimeter; full proceeds are not fresh income. |
| AMM returns | Adjust LP value changes for principal flows and reconcile collected/uncollected fees. Issuer rewards already booked elsewhere must not be included again. |
| Receipt allocation estimates | Any spreading of receipt-based revenue over earning periods belongs only to reporting and must retain its estimate/source evidence. |

### Spark issuer rewards

Accrue daily using end-of-day eligible balances and act/365. Do not apply these
rates to every balance bearing the same symbol.

| Issuer | Eligible balances and rate |
|---|---|
| RLUSD | 4.5% APR on raw ALM RLUSD, the ALM's unborrowed SparkLend RLUSD (`supplied × (1 − utilisation)`), and RLUSD in its open Uni v4 RLUSD/USDS positions. For the Morpho RLUSD vault, only `max(4.5% − net vault APR, 0)`; vault APR is net of the performance fee. |
| PYUSD | APY = DFEDTARL − 0.625 percentage points + 1.8 percentage points; use 2.575 instead of 1.8 before 2026-03-09. Convert to APR as `365 × ((1 + APY)^(1/365) − 1)`, with APY expressed as a fraction. Apply to raw ALM PYUSD, its unborrowed SparkLend share, and its open Uni v4 PYUSD/USDS positions. Include its pro-rata Curve PYUSD/USDS PYUSD leg only through 2026-06-30. |
| USDG | Payments occur every eight hours: use actual qualifying reward receipts as the earned-revenue approximation at a cutoff. Do not invent an additional receivable from a hypothetical rate. |

Raw RLUSD/PYUSD/USDG at the ALM are allocations under the agreed cash perimeter.
Reward receipts there are income/accrual settlements, but not cash receipts.
Convert-to-cash events must be separately identified. Savings-wallet roles are
not automatically governed by the ALM classification.

Public identifiers (never place RPC URLs, API keys or passwords in this file):

| Purpose | Identifier |
|---|---|
| Ethereum ALM | `0x1601843c5E9bC251A3272907010AFa41Fa18347E` |
| Morpho RLUSD vault | `0xFC8C624B6080a0a780583799f2A862DE936F6E22` |
| Uni v4 RLUSD/USDS pool | `0x9035721b23481db3888fd201b9c2b26dbc3af60258bca65e669f2ed98dc8eb4f` |
| Uni v4 PYUSD/USDS pool | `0xe63e32b2ae40601662f760d6bf5d771057324fbd97784fe1d3717069f7b75d45` |
| RLUSD reward payer | `0xb70e0fad7d0cf4c187365a84e728802ab24ca78b` |
| PYUSD reward payer | `0xfc0539d019482d311c161ae3b756cdccdec45e87` |

### Spark treasury and nonstable assets

* **SparkLend fees:** transfers of reserve-factor proceeds from the reserve
  treasury to the reporting perimeter, including nonstable reserve tokens swept
  through Ops. Recognize value received once; routing onward to the ALM and sale
  proceeds are not additional fee income. Later price changes belong in gains.
* **MORPHO and AVAX rewards:** recognize the agreed reward receipts/earnings, then
  remeasure through the corresponding nonstable gains lines. Include Ops-wallet
  holdings, native AVAX and wrapped AVAX without treating wrapping as a sale.
* **SparkLend non-USD reserves:** retain asset gains. Do not subtract savings
  depositor liability revaluation here or in SparkLend fees; savings liabilities
  have their own funding lines. Never count the same liability twice.
* **Curator fees:** distinguish receiving a vault's performance fees from the
  return earned by supplying to that vault. A fee-recipient address alone is not
  evidence that the prime receives the fee; verify ownership and actual rights.

## Expenses, own tokens and savings

| Item | Rule |
|---|---|
| Contributor SPK | Compensation and benefits, using approved monthly expense-sheet figures. A vesting-wallet transfer is evidence, not an automatic second expense feed or proof of its service period. |
| Spark Points/Overdrive SPK | Prime token incentives, separate from contributor compensation. Keep campaigns disjoint from compensation. |
| Noncash SPK expense | Include once in income; add back once in indirect operating cash flow. Exclude from cash-payable expense accruals and cash expenses paid. |
| Pending compensation sheet | Do not substitute transferred token quantities for a missing approved USD expense. Preserve the missing/provisional component explicitly. |
| Expense presentation | Keep the accounting calculations detailed, but show two levels of Spark expenses; hide the staff-function breakdown. |
| Savings liability accrual | Measure depositor interest using the applicable onchain vault rate/index and actual deposits/withdrawals. Keep the liability separate from asset revenue and price gains. |
| Savings interest paid | Operating cash flow on its own line. Remove the same payments from general Expenses paid. Principal deposits/withdrawals belong to financing, not interest. |

## Conversions, loans and Sky direct exposures

* USDS sent through the Sky PSM wrapper and the matching USDC returned are a
  conversion, not an operating expense. Pair both legs; do not hide unmatched
  movements behind a blanket expense classification.
* Loan repayments must be split into principal (investing) and interest
  (operating). Spark's 2026-07-21 Anchorage return of 10,000,017.287194 USDC is
  principal; reuse the existing MSC correction as evidence, without changing MSC.
* Bridged/in-transit cash remains attributed to the source chain until arrival
  for reporting; reconcile both legs and prevent duplicate cash recognition.

### Sky direct exposures (SDE)

Atlas defines SDE as Sky-owned exposure implemented through a prime's allocation
system. Ownership, funding and cash routing are distinct questions.

| Area | Rule |
|---|---|
| Revenue | Exclude Sky's yield from the prime's income. Retain the prime-owned portion. Do not classify a whole pool as SDE merely because one leg is designated. |
| Funding cost | Exclude only the designated SDE exposure from the chargeable funding base while the designation is effective. |
| Cash flows | Preserve actual movements within the measured wallet cash scope. Separately identify deployments/redemptions and funding executed on behalf of Sky. Do not blindly remove matching gross amounts from allocation and borrowing lines. |
| Prime-funded deployment | A deployment from existing prime cash is real. Establish any corresponding Sky funding/settlement entitlement from evidence; do not invent an equal borrowing or receivable. |
| Mixed ownership | Attribute the designated leg/portion to Sky and the rest according to its own terms. Keep gross, Sky and prime amounts in API evidence. |
| Dates | Apply historical designation and ending dates, including partial months. Changes of designation without token movement are noncash, not invented subscriptions/redemptions. |

Spark-specific scope:

* Uni v4 **USDT** in USDS/USDT is designated; **PYUSD in PYUSD/USDS is not**,
  and RLUSD in RLUSD/USDS is not. Idle USDS exclusions are a separate mechanism.
* Curve **USDT** in sUSDS/USDT was designated 2025-11-13 and ended 2026-06-25.
  Do not keep it active indefinitely or remove its historical designation.
* Atlas lists Uni v4 USDT designation as 2026-05-26. Older config starts earlier;
  use authoritative designation history, not first LP mint, to establish dates.

Reporting overrides live in `spark_ownership.py`; the legacy MSC SDE table is
not silently rewritten by reporting repairs. API `allocation_split` uses
`effective_dated_designated_exposure` for the new ownership calculation. Its
`sky_share` is the effective income ratio, not a constant capital-ownership
percentage; gross, Sky and prime amounts are the reconciliation controls.

References: [SDE definition](https://sky-atlas.io/#b3fb8653-8503-4a9e-81b2-5e9f49ad6703),
[revenue/funding rules](https://sky-atlas.io/#07e0f716-ce23-4394-a5f4-bee537713f48),
[historical Curve record](https://github.com/sky-ecosystem/next-gen-atlas/commit/93f7f49476912f982acc235311cae9ee07ae394e).

## Implementing another prime

1. Establish its wallets, ownership boundaries, cash assets and separate venue
   settlement/reporting eligibility. Do not copy Spark's wallet/token roles.
2. Map each revenue stream to its earning method, principal flows, reward stream,
   and any third-party ownership. RWA allocations can be grouped as vaults in UI.
3. Import an approved versioned expense source. Distinguish cash expenses,
   contributor compensation and user token incentives before calculating totals.
4. Reconcile cash opening + observed change = closing, and independently compare
   statement cash flow to measured change. A zero gap alone does not prove that
   every transaction is correctly classified.
5. Test internal transfers, principal returns, reward accrual versus receipts,
   token sales, SDE effective boundaries, noncash compensation and missing inputs.
6. Review API and dashboard totals together, preserve source metadata/exports,
   and replay reporting publications when methodology changes. Do not replay MSC
   as a side effect of a reporting correction.

### Updating contributor compensation

`contributor_compensation.json` is the current authored input. Approved monthly USD amounts replace pending nulls; token transfer quantities are never a substitute. Before publishing a changed input, archive the complete JSON under `published_expenses/<verification.digest(input)>.json`. Keep old archives: the statement selection pins `expense_input_revision`, and cash flow dependencies pin the same input. The dashboard reads this component from the API rather than maintaining a second contributor expense feed.

## Prepared November 9 review policy

The opt-in Spark v3.11/events.v2 implementation and its audit evidence are
recorded in [SPARK_REVIEW_2026_11.md](SPARK_REVIEW_2026_11.md). Until reviewed and
rolled out, archived/default policies keep their existing treatment.

- Net matched Savings-funded ETH principal FX out of gross asset gains and
  funding cost. Retain actual depositor interest and Spark-owned price exposure.
  Remove the matching amounts from both indirect cash-flow reversals.
- Fees paid by a prime's own vault supply are an allocation rebate; external
  curator fees exclude that self-funded portion. Net supplier NAV yield plus
  the rebate plus external fees must preserve the previous aggregate revenue.
- The provisional Spark contributor grant input is 1,627,925 USD monthly under
  the supplied grant-date/48-month schedule, not the amount of vesting claims.
  When included in a later expense sheet, suppress the separate expense feed.
- SDE cash subscriptions/redemptions need authenticated liquidity principal
  evidence. Swaps through the same pools remain allocation cash. Moving between
  those investing rows must conserve total investing cash.
- Expanding a controlled cash cluster requires new opening/closing balances
  and event coverage; do not reclassify old inflows without also measuring the
  added wallets' cash and onward payments. Keep unidentified receipts visible.
- Do not combine reviewed income with legacy cash scope, or publish a mixture
  of cash-policy cohorts as though their monthly balances were comparable.
