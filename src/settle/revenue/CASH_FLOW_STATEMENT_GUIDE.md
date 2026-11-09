# Building a prime cash flow statement

Updated after the 9 November 2026 review. Read [ACCOUNTING_RULES.md](ACCOUNTING_RULES.md) and [INCOME_STATEMENT_GUIDE.md](INCOME_STATEMENT_GUIDE.md) first.

This guide describes how to extend Spark's implementation to Grove, Osero or another prime. It covers the Reporting API and dashboard. Cash-flow reporting must not change MSC settlement calculations.

Reference implementation: [settlement-cycle PR #254](https://github.com/soterlabs/settlement-cycle/pull/254) and [reporting-dashboard PR #18](https://github.com/soterlabs/reporting-dashboard/pull/18), merged after two review/fix rounds.

## 1. Agree the accounting perimeter first

| Decision | What to record for the new prime |
|---|---|
| Owned cluster | Full addresses and chains for ALMs, SubProxy, Foundation/DevCo, operational and treasury wallets, Savings idle balances and payout queues. Confirm ownership rather than inferring it from transfers. |
| Cash | Explicit stablecoins and wallet roles considered cash, plus denomination/valuation rules. Allocated stablecoin principal is an investment, even when its token is stable. |
| Rewards | Identify verified reward payers. Separate reward cash from principal in mixed issuer-stable balances; do not classify an entire RLUSD/PYUSD/USDG holding as rewards. |
| Internal movements | Transfers within the cluster, converters and bridges are routing. They are not revenue, expense, borrowing or principal allocation solely because a Transfer exists. |
| Financing | Separate net Sky debt, depositor principal and buybacks. Depositor interest paid belongs in operating cash flow. |
| Noncash funding | Exclude directly funded noncash seeds from cash flow only when their funding is demonstrated. Include both financing and investing legs when cash actually passes through the cluster. |
| Opening balances | Independent opening cash, accrued receivables/payables and outstanding bridge claims at the reporting start. Do not assume an empty opening position. |
| Unknowns | Acknowledge unresolved owners/payers explicitly. Keep their cash movements in reconciliation; do not force them into an income/expense category. |

Use one consistent cluster for the income statement, cash-flow bridge and balances. If the income report covers only an ALM subset, disclose the missing perimeter and retain partial coverage.

## 2. Map each row to evidence

| Cash-flow line | Data and calculation | Typical non-chain dependency |
|---|---|---|
| Net income | Retained Reporting API net revenue less that prime's approved expenses, using the same cutoff as the cash-flow month | Expense records; offchain accrual inputs |
| Own-token incentives | Add back the same noncash token expense already deducted from net income, once | Approved grant/service-period or programme expense and valuation; SPK/GRV claim dates are not service expense timing |
| Nonstable gains/losses | Reverse only genuine noncash gains remaining in net income; do not reverse matched Savings principal FX already excluded | Historical prices and complete position scope |
| Nonstable receipts/disposals | Full transaction history and historical cost basis across owned wallets; transfers preserve basis | Classification of rewards vs investments; unsupported custody records |
| Other noncash adjustments | Reverse identified accruals included in net income, such as depositor interest owed but not paid; exclude matched principal price revaluation removed from income | Explicit mapping to the income report |
| Earned but not received | Retained earned entitlement/receivable movement, with true-ups and opening receivable tracked | Approved settlement components, servicing statements or contractual terms |
| Sky allocation/demand receipts | Actual dated cash events matched to executed settlements; split components using approved source amounts and retain actual cash total | Published settlement record and execution mapping |
| External operating receipts | Verified reward/interest/other operating receipts, excluding allocation principal | Payment attribution where transfers do not distinguish principal from interest |
| Expenses booked / paid | Add back cash-payable booked expenses; deduct actual external cash payments. Do not count internal wallet funding as payment | Approved expense import and payee/category mapping |
| Allocation changes | Subscriptions and redemptions of principal. Split redemption yield from capital before certifying the amount | Offchain/RWA redemption statements where token events are insufficient |
| Investments | Actual investment cash payments/proceeds, distinguished from allocation subscriptions | Equity/SAFE classification and agreed exclusions |
| Net Sky borrowing | Actual draw/repayment cash legs and verified debt attribution | Reviewed direct-seed funding rules |
| Savings deposits/withdrawals | Complete Deposit, Withdraw and share-transfer history; carry weighted-average deposited principal with transferred shares. Separate principal financing from interest operating cash | Queue/request mapping for delayed payouts; product-specific rules |
| Buybacks | Actual stablecoin spending with verified own-token purchase evidence | Buyback venue/perimeter mapping |
| Opening/closing cash | Independent historical balance reads at certified cutoffs | Reviewed cash-token registry and reward/principal subledger |
| Cash in transit | Authenticate bridge messages across both legs. Until arrival, retain the claim on the source chain without also counting destination cash | Protocol-specific bridge identifiers and finality rules |
| Reconciliation | Compare independently measured change with statement flows; expose gaps and unknown movements | Investigation and sign-off; never a balancing plug |

For Grove, RWA vaults can share the allocation grouping, but share transfers/mints alone do not establish cash received or earned revenue. Combine actual settlement-token cash events with the supplied servicing/accrual records. Keep estimates provisional when historical principal, fees or redemption attribution is missing.

Onchain events cannot by themselves establish offchain accrued revenue, every expense category, the principal/interest split of an unexplained bank-loan payment, or opening offchain receivables. Publish supported components and name the gaps.

## 3. Extend the Reporting API

The current implementation is intentionally **Spark 2026 only**. Adding a JSON config alone does not enable another prime; remove the Spark-specific assumptions explicitly.

| Location in `settlement-cycle` | Required change |
|---|---|
| `src/settle/revenue/cash_flow/config.py`, `spark.json` | Load a reviewed per-prime policy, owned cluster, chain-qualified tokens/counterparties, Savings contracts and opening receivable. Keep provider credentials in environment variables only. |
| `layout.json` | Define the new statement rows, hierarchy and stock/flow aggregation. Reuse shared rows where appropriate. |
| `settlements.json` | Add prime-qualified approved settlement execution mappings and public source evidence. Match actual cash; do not assume that mint date equals the earned month. |
| `collector.py`, `evidence.py` | Reuse certified cutoffs, transfer collection, historical balances, complete Savings histories and strict scope checks. Add reviewed source/contract support for extra chains. Missing RPC/token coverage remains unavailable. |
| `classify.py`, `liability.py` | Replace Spark-specific addresses/rules with reviewed per-prime mappings. Implement product-specific principal/interest, rewards, queue and bridge attribution. Each cash leg is consumed once. |
| `build.py` and expense input | Select the new prime's retained income publications and expenses. Never copy Spark's expense values. Full-month provisional expenses must not be paired with MTD/stale income. |
| `model.py`, `project.py` | Enable the new prime/policy and validate signed subtotals, missing/partial values, cumulative receivables and opening/closing balance selection. |
| `store.py` | Retain append-only publications, source/evidence hashes and configuration/code revisions. Existing storage has a prime key; keep selection scope strict. |
| `src/settle/api/cash_flow.py` | Enable the reviewed prime/year in annual, transaction-evidence and balance-witness routes. Reads stay provider-free. |
| `job.py`, `.railway/railway.ts` | Add prime/year CLI selection, a separate advisory-lock scope and a dedicated recurring service with secure provider references. Preserve existing services. |

Current endpoint pattern:

| Endpoint | Purpose |
|---|---|
| `/v3/reporting/cash-flow?prime=spark&year=2026&review=true` | Latest annual statement, monthly coverage, methodology and source manifest |
| Same route with `revision=<selection>` | Immutable annual replay, preserving months that were absent |
| `/v3/reporting/cash-flow/events?prime=spark&month=2026-10&revision=<monthly revision>` | Paginated transaction evidence; optional line filter |
| `/v3/reporting/cash-flow/balances?prime=spark&month=2026-10&revision=<monthly revision>` | Pinned independent opening/closing balance witnesses |

The annual selection and monthly revision are different identifiers. Save the annual selection for CSV; use each month's revision to retrieve its evidence.

`available.value` means complete within the declared scope; `partial.known_value` is a known component/subtotal; `unavailable` means TBD. Missing is never zero. Delayed MTD publications must also keep YTD amounts partial. Closed months require month-end publications. Opening/closing annual balances require the correct boundary month rather than a convenient fallback.

## 4. Enable the dashboard

| Location in `reporting-dashboard` | Required change |
|---|---|
| `src/lib/cash-flow-api.ts` | Add the new prime/policy, reviewed opening balance and row identities. Retain hash, scope, cutoff, hierarchy, subtotal and cumulative-balance validation. |
| `src/lib/api.ts` | Reuse the stored Reporting API reader and retained-failure behavior. |
| `src/components/prime-dashboard.tsx` | Enable the Cash Flow Statement tab for the new prime and select its API report. |
| `src/components/cash-flow-statement.tsx` | Use API row metadata and indentation. Update Spark-only headings/fallback layout for the new prime. Keep groups collapsed by default. |
| `src/app/api/export/route.ts` | Enable prime/year export with the exact annual selection shown on screen. Never substitute another prime or a workbook snapshot. |
| `src/app/api/cash-flow/{events,balances}/route.ts` | Enable the new prime with strict evidence selection validation. |
| Fixtures/tests | Add actual reviewed source examples, absent/partial months, delayed MTD, missing boundaries and invalid/cross-prime selections. |

Display whole dollars without dollar signs in cells, using the shared neutral number styling. Append `*` for provisional/estimated amounts under the current display convention; retain recognition and quality in the API/evidence rather than restoring green/blue/orange accounting-basis colours. Missing is TBD and future periods are dashes. Automatic refresh should replace TBDs when publications arrive; retained failures stay source-qualified and provisional.

Operating, investing and financing activities are category captions: no number is expected. Do not give a section caption the role of an expandable numeric parent. Keep investing rows such as Sky-direct deployments and treasury proceeds at `parent: null`, consistent with the other investing rows; use parent/child links only for genuine drilldown groups. Reuse the common statement widths, fonts, section styling and subtotal separators.

## 5. Validate and roll out

1. Capture one closed month as a read-only preview. Review representative subscription, redemption, reward, repayment, Savings and bridge transactions using full addresses and transaction hashes.
2. Reconcile independent cash balances to all external cash legs, then to the economic statement. Show unknown movements/gaps; do not manufacture zeroes or force a match.
3. Check net income against the income statement, interest against the Savings liability bridge, and every subtotal against its children. Check no principal is classified as revenue and no reward/interest is counted twice.
4. Test immutable annual replay, pinned CSV, source pagination, historical cutoff selection and API outage recovery. API reads must never perform provider calls or write MSC results.
5. Create backend and dashboard PRs; review/fix twice. Merge/deploy the backend first.
6. Publish historical months using the merged code and reviewed configuration. Preserve source hashes, certified pins and code versions. Warm the durable public-evidence cache.
7. Enable and verify the dedicated recurring job, then merge/deploy the dashboard. Check live figures, estimate markers, evidence links, CSV and automatic TBD replacement.
8. Record remaining gaps and their owners. Replay affected months after corrected source data/classification arrives; retain old publications for audit.

Spark's dedicated job is configured for **22:15 UTC daily**, refreshing the previous month-end and latest completed day. Older corrections need an explicit historical replay. The initial capture covers January–October 2026 plus November 4 on seven supported chains; XLayer/BSC/Gnosis remain missing. Authenticated bridge transit, mixed reward cash balances, broader nonstable cost basis, queued Savings payouts and Sky-direct funding attribution remain explicit gaps. Do not carry these limitations into another prime as assumed zeroes.


## Spark rollout verification (5 November 2026)

Both reference PRs are merged and their API/dashboard deployments are live. Eleven publications are available: January–October month-ends and November 4 MTD. Source captures for seven configured chains are complete and 77 chain/cutoff captures are retained in the durable source cache. Missing chain support and economic attribution gaps still prevent a fully reconciled statement.

Historical checks on November 5 validated API hashes/subtotals, transaction and balance evidence, pinned CSV, the then-current confidence colours, initial collapsed groups and mobile scrolling. Those colours are not the current display instruction. Cash-flow net income matches the displayed income statement for all ten supplied expense months; November expenses/net income remain TBD. The recurring service has the reviewed `15 22 * * *` UTC schedule. Local outage/recovery checks confirmed automatic TBD replacement and provisional retained data after a failed refresh.


## November review controls to apply to every prime

| Case | Rule / check |
|---|---|
| Principal versus yield | Loan/redemption principal is investing; interest is operating. A USD valuation change in a non-USD principal balance is not a cash event or depositor interest. |
| Matched Savings principal FX | Remove the same paired amounts from the gains reversal and liability noncash add-back. The paired correction cannot change operating cash. Retain actual VSR interest and interest paid. |
| Curator self-fees | Reclassifying internal fees between allocation yield and external curator revenue leaves net income and cash unchanged. Minted fee shares are noncash; do not fabricate a cash receipt/subscription. |
| Contributor SPK / GRV | Use the same approved/provisional expense as income, add it back once if noncash, and exclude it from cash-payable expenses. A grant claim/internal transfer is not a second expense or cash payment. Obtain each prime's own grant terms. |
| Issuer stablecoins | A token that is an allocation at an ALM remains an allocation when rewards arrive there. Accrue income under programme rules; reverse noncash issuer income in the indirect bridge. Only actual conversion into the cash perimeter produces cash. |
| PSM/wrapping | Match both conversion legs; neither is contractor expense or fresh revenue. Keep ambiguous routes incomplete. |
| SDE pool activity | Only authenticated liquidity subscriptions/redemptions enter cash deployed on behalf of Sky. Swaps stay in allocation cash. Use designated leg/period only; moving between these investing rows must preserve their total. |
| Expanded cluster | Collect fresh opening/closing balances and complete events for added wallets. Eliminate own-wallet transfers and classify onward payments; never erase an old outflow without measuring the receiving wallet. Owner overlap is evidence, not sole proof of entity control. |
| Unidentified movements | Remain visible in reconciliation/coverage. Generic operating-wallet payment expense classification stays provisional unless supported by counterparty evidence. |
| Publication cohorts | Pair reviewed income, cash scope and expense revisions; keep old policy selections reproducible. Never fill missing reviewed months using incompatible legacy balance scope. |

Example: removing 10,000 of matched asset/liability principal FX removes both
the 10,000 gains reversal and the 10,000 liability add-back. Cash does not change.
Contributor compensation of 100 reduces net income by 100 and adds back 100;
claiming the grant later is not an additional operating cash outflow.

For Spark use the v3.11 income policy and events.v2 cash policy together. Legacy
queries/selections retain their own policy. The default CLI/HTTP legacy opt-in
must not be mistaken for the new policy: use `--review`, `review=true`, or the
configured reviewed worker flag as appropriate. See
[SPARK_REVIEW_2026_11.md](SPARK_REVIEW_2026_11.md) for the rollout and audit.

Before deployment: publish/verify the source and cash history, including the
latest completed UTC day; only then deploy a dashboard that opts into the new
policy. If an exact MTD component source does not exist, collect it normally at
that cutoff before running retained-source completion. Scheduled source workers
must generate the same reviewed inputs as the backfill worker. Verify a fresh
API selection, not only an old pinned export. Preserve missing statuses and
provisional classifications; a zero reconciliation gap alone cannot certify
correct categories or complete scope.

### Treasury vault subscriptions and accrued yield

Use retained `steakhouse` evidence for the treasury-only ERC4626 example.
Match actual owned cash transfers even when routed through a bundler, classify
subscription cost as investing, and split redemption principal from realized
yield using the carried share basis. Reverse the same monthly native accrual
from net income in the income-to-cash bridge before adding realized operating
receipts, so income and cash are each counted once. A cash-to-cash stablecoin
conversion, including an explicitly verified B2C2 transaction pair, is internal
routing; leave any unexplained shortfall outside classified statement totals.
