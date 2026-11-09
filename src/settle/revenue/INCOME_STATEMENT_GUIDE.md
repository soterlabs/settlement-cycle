# Building a prime income statement

Updated 9 November 2026 after the Spark review. Start with
[ACCOUNTING_RULES.md](ACCOUNTING_RULES.md); the
[cash-flow guide](CASH_FLOW_STATEMENT_GUIDE.md) uses the same income selection.
Apply the principles to Spark, Grove, Osero and other primes, but establish each
prime's own contracts, ownership, grant terms and source coverage.

## 1. Define each source and period

Record the reporting entity's full wallet/chain and offchain position scope.
Enable reporting independently from settlement eligibility. RWA positions may be
presented as vaults, but token activity alone does not establish accrued revenue.
Preserve contract/service records and use estimates when terms/history are gaps.

Every leaf must have a reporting API metric, a period, its recognition method,
quality/status and retained source revision. A configured venue is not evidence
of zero income. Do not sum daily estimates on top of an authoritative monthly
source. DR remains missing until the approved monthly publication exists.

## 2. Separate principal, yield and price movements

| Component | Income statement | Evidence/control |
|---|---|---|
| Principal subscription/redemption | Exclude from revenue | Pinned balances and actual capital events; loan principal overrides |
| Native supply/LP/RWA return | Allocation revenue | Closing position − opening position − subscriptions + redemptions, with event-time valuation; separate reward tokens and price effects |
| Interest on a non-USD asset | Allocation revenue, translated to USD | Interest units earned, priced at the supported daily/event methodology; closing total position value is not revenue |
| Prime-owned asset price change | Net gains on non-stable assets | Flow-adjusted daily/transaction revaluation; exclude income already recognized on receipt |
| Matched Savings-funded ETH principal FX | Net out of gross management gains and funding cost | Match backed asset and depositor claim units at the same boundaries/prices; retain both gross audit metrics |
| Residual own ETH/reserve exposure | Retain genuine price gains/losses | Net ALM asset FX less matched depositor FX; retain separate own Ops gains |
| Savings depositor interest | Cost of capital | Reconciled VSR/index/Drip interest units and pending interest, translated to USD; exclude depositor principal and its matched principal FX |
| Fees/rewards received in nonstable tokens | Income at earned/receipt value under the approved method | Subsequent changes go through gains; selling later does not recreate the original revenue |

Example: 100 ETH of depositor principal backed by 100 ETH appreciates by 10,000
USD. Asset and liability price effects offset; neither is interest revenue/cost.
If interest owed grows by 1 ETH, its USD value remains funding expense. Any ETH
exposure owned by the prime above the backed depositor claim still has gains.
Do not describe this correction as removing principal that had been booked as
revenue: principal balances were excluded already; their price revaluation was
the gross presentation issue.

## 3. Handle curator self-fees once

| Item | Treatment |
|---|---|
| Vault yield before a performance fee | Do not add a self-fee rebate again |
| Vault yield after a performance fee | Add back the portion returned to the prime on its own supplied capital |
| External-depositor fee portion | Curator fees |
| Own-deposit fee portion | Same vault's allocation revenue, as a reversal of the internal deduction |
| Fee shares minted to controlled recipient | Income in kind/entitlement evidence, not cash received or new supplied principal |

100 of underlying interest less 10 paid back to the same reporting entity is
90 net vault yield + 10 own-deposit rebate = 100 allocation revenue. Moving 10
out of gross curator fees into that allocation leaves total revenue unchanged.
Fees paid to an outside recipient remain a real cost. External fees received
are additional revenue from external capital, not a self-charge.

Authenticate fee mints and value at event NAV. Split with controlled share
ownership immediately before the fee mint; account for V1/V2 event ordering and
pending unminted entitlement at both boundaries. Reconcile transfer histories to
onchain supply/balances. Keep attribution provisional where scope/method is
unconfirmed. Public dashboard totals are cross-checks, never balancing targets.

Spark's API retains net NAV yield/supply rates in `native_revenue_usd`, a separate
`self_curation_rebate_usd`, and their sum in `allocation_revenue_usd`. Family
allocation totals use the sum. Fee ledger gross entitlement is evidence;
`external_curator_revenue_usd` is the statement amount. Never sum gross ledger
fees again on top of the two economic revenue buckets.

## 4. Own-token compensation: SPK, GRV and others

| Question | Required treatment |
|---|---|
| Contributor services or user incentive programme? | Compensation and benefits for services; incentives expense for user programmes. Keep beneficiary/programme sets disjoint. |
| What is the expense period and valuation? | Approved grant terms: total award, valuation basis, service start/end, vesting/conditions, amendments and cancellations. Claims do not define service expense. |
| Spark's provisional grant | 1.2bn SPK × 0.065117 USD / 48 months = 1,627,925 USD monthly, June 2025–May 2029. In this implementation only 2026 is exposed; MTD is calendar-day prorated and future cells stay blank. |
| Grove/GRV | Obtain Grove's grant terms and valuation policy. Do not copy Spark's price, quantity or schedule, or equate every GRV transfer with compensation. Unknown remains TBD. |
| Expense sheet later includes the grant | One source of expense: set the explicit inclusion flag for the new archived sheet revision; suppress the extra expense feed and retain the matching noncash add-back. |
| Native token cash purchase | Actual cash transaction in its own category; never add it as a second service expense solely because tokens were acquired. |

Archive authored inputs under their content digest. Income selections and cash
flow dependencies pin the same expense-input revision. Missing or provisional
grant data must retain its status; do not silently assume zero or certify costs.

## 5. Build the statement and verify

| Statement row | Calculation/source |
|---|---|
| Demand-side revenue | Published agent rate and DR; GAR only for an eligible prime (currently Skybase) |
| SparkLend/protocol fees | Verified reserve treasury receipts, including nonstable receipts valued once |
| Curator fees | External fees only, as above |
| Other incentives/rewards | Programmes not tied to an allocation; exclude rewards already included below |
| Net gains on non-stable assets | Genuine own price gains/losses, after matched Savings principal FX removal |
| Noninterest revenue | Sum disjoint demand, fees, other rewards and gains |
| Allocation revenue | Native yield + relevant own-fee rebate + allocation-linked issuer/lending rewards, each once; exclude Sky-owned yield using effective dates |
| Cost of capital | Chargeable Sky funding plus depositor interest, excluding matched Savings principal FX |
| Net interest income | Allocation revenue − cost of capital |
| Total net revenue | Noninterest revenue + net interest income |
| Total noninterest expense | Approved cash/noncash expenses, including contributor compensation once |
| Net income | Total net revenue − total noninterest expense |

Category captions have no amount. Hidden dust rows retain their underlying
revenue in totals/API evidence; hiding them must not erase historical data.
Gains are signed; negative values use minus signs. Match comparable YTD revenue
and expenses to the last common supplied month without suppressing live MTD
revenue cells. Use source revisions for exports and refresh live API reads so
new publications replace TBD without manual UI edits.

Test: capital-flow neutrality; interest versus price effects; source before/after
fees; gross-fee split conservation; token compensation exactly once; old selection
replay; effective ownership dates; missing ≠ zero; no orphan layout rows. Removing
matched ETH gross FX and reclassifying self-fees must not alter net revenue.
Contributor compensation lowers net income and is added back once in cash flow.

## Source locations / rollout

`financial.py`, `financial_spark_review.py`, `spark_curator_attribution.py`,
`statement_rollups.py`, `spark_reporting_job.py`, `expense_inputs.py`, and
`cash_flow/build.py` implement the Spark reference. Use
[the review audit/runbook](SPARK_REVIEW_2026_11.md) for reviewed v3.11/events.v2
rollout, source/cash backfill and legacy-policy preservation. No MSC replay is
part of a reporting methodology correction.
