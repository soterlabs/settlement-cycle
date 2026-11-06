# Financial reporting API for the prime-agent dashboard

Status: first slice implemented in PR #229; slice 2A allocation coverage is implemented in the next stacked PR. Broader cost/yield, RWA and consolidation work remains proposed. Date: 2026-10-28.

The implemented contract and backfill instructions are in [the financial API guide](pipeline/financial-reporting-api.md).

## 1. Outcome and first delivery

Replace useful dashboard `TBD` cells with explicitly defined, traceable financial
amounts. Start with individual-prime management reporting and a small approved
set of aggregates. Expand coverage without requiring a complete income statement,
legal-entity model or all historical data before shipping anything.

The first release should deliver:

1. Agent-rate income and a separately identified Sky funding-interest component
   from saved calculations, with their actual reporting dates.
2. One approved investment-income mapping, starting with Obex's Maple syrupUSDC
   position, and its tracked allocation value. Confirm the net-yield measurement
   policy before labelling it interest income.
3. Backend-owned totals for that declared, non-overlapping portfolio scope.
4. Monthly statement cells with independent availability, so unknown DR, expenses
   or tax do not hide known AR or investment figures.
5. A small dashboard integration that actually binds those cells and exports.

Success is several correct numbers in the existing screens, even if much of the
annual statement remains unavailable. This is not a prerequisite to, or a change
in, MSC settlement recognition. Do not replace financial reporting totals with
MSC `monthly_pnl` or equate it with income before tax/net income.

## 2. Evidence and current gaps

The product inspected is **soterlabs/reporting-dashboard**, main commit
[`2637efe`](https://github.com/soterlabs/reporting-dashboard/commit/2637efedf441d8ffaf551b5f4ed86b802e271d6d).
The API baseline inspected is settlement-cycle `e8c29f6` (merged PRs #227/#228).

| Current implementation | Consequence |
| --- | --- |
| [`prime-reports.ts`](https://github.com/soterlabs/reporting-dashboard/blob/2637efedf441d8ffaf551b5f4ed86b802e271d6d/src/lib/prime-reports.ts) defines statement rows, scopes, month selection and YTD/FY rules | The API needs an explicit statement taxonomy and period contract, not just another venue list. |
| [`income-statement.tsx`](https://github.com/soterlabs/reporting-dashboard/blob/2637efedf441d8ffaf551b5f4ed86b802e271d6d/src/components/income-statement.tsx) renders every financial cell using `money(null)`; statement CSV export also hardcodes TBD | API work alone cannot populate these cells. Binding the component/export is a required integration task. |
| [`api.ts`](https://github.com/soterlabs/reporting-dashboard/blob/2637efedf441d8ffaf551b5f4ed86b802e271d6d/src/lib/api.ts) reads only v2 reporting latest/at, and [`reporting.ts`](https://github.com/soterlabs/reporting-dashboard/blob/2637efedf441d8ffaf551b5f4ed86b802e271d6d/src/lib/reporting.ts) rejects publications unless `result.totals === null` | Filling the existing v2 `totals` field would break current consumers. Use a new contract and adapter. |
| Portfolio rows already render published opening/closing values and supported cash receipts | These do not all need new extraction. The principal gaps are classifications, aggregate scope, recognised accrual, cost attribution and coverage. |
| The frontend treats only `method === loan_accrual` as certified accrual income/receivables | Supported vault/lending yield and issuer incentive accruals need explicit recognition metadata; changing numbers alone will leave those cells TBD. |
| Group/combined views show source portfolios without consolidation; there are no Sky-wide income screens | Scope is the six prime agents. Do not introduce a Sky income statement or use Sky's income as prime income. |

Live read-only observations during preparation: Grove, Obex, Keel, Osero and
Skybase had compatible reporting publications through October 26. Spark's latest
v2 reporting request returned 409, while its legacy stored result remained
readable. Availability can change as jobs progress. Publication/backfill coverage
is an operational dependency, separate from financial classification.

### A concrete early win

Obex's saved October 1–26 result already contains the following inputs:

| Input | Observed amount, USD | Proposed first use |
| --- | ---: | --- |
| `agent_rate` | 70,438.27994360848908769110354 | AR statement leaf; accrued protocol entitlement. |
| V1 `revenue` / `actual_revenue` | 1,460,382.34398988582000 | Maple net investment-income leaf, once its recognition/classification is approved. |
| `sky_revenue` with `sde_revenue = 0` | 1,122,790.648948437946354221901 | Identified Sky funding-interest component; not proof of all entity financing expense. |
| V1 `value_eom` | 406,996,930.93281591899000 | Tracked allocation value for the approved single-position perimeter. |

Source cutoff: `2026-10-26`; revision:
`f7e10d1cde14f19d29b5f7b0e3cf2e8167dd6f5e942aa479849fa225b823d072`;
original result hash:
`3acf10280720fd3c57f3e3806b74b2d661ecacca367066eef26c391dfb5f49e0`.
These are observed inputs, not an approved entity-wide financial statement.
The frontend should continue using only the reporting API; the backend may reuse
these internally stored inputs without making the dashboard call settlement APIs.

## 3. Product scope

The dashboard has two views and three reporting scopes:

| View/scope | Initial promise | Later promise |
| --- | --- | --- |
| Individual / income statement | Known monthly components for an explicitly named operational prime perimeter | More classified income, expenses and complete subtotals. |
| Individual / portfolio | Source positions plus an approved tracked-allocation aggregate | Cost attribution, yield and broader balance-sheet coverage. |
| Prime group | Published source amounts and an explicit unavailable consolidation status | Prime plus controlled entities, with effective-dated membership and eliminations. |
| Combined primes | Aligned source-prime contributions; clearly labelled known subtotals where permitted | Approved combined results with inter-prime eliminations. |

An operational prime perimeter is useful before a legal-entity perimeter is
complete. Label it as such. A sum before eliminations is an **aggregate**, not a
consolidated group financial statement. The API must distinguish the two.

Not required for the first release: full statutory reporting, an expense ledger,
tax estimation, forecasts, a balance sheet, all January–December data, universal
loan accrual support, or changes to the current MSC calculation.

## 4. Dashboard-to-API requirements

### 4.1 Income statement

Return stable semantic line IDs, labels, parent/child relationships, ordering,
units and per-month metrics. Do not make the current positional Spark IDs such
as `allocation-0-1` the permanent backend identity. Version the taxonomy and
provide a frontend adapter from its current rows.

| Current dashboard rows | API requirement / source | Delivery |
| --- | --- | --- |
| AR (`ar`) | Saved `agent_rate`, with recognition and applicability; no new chain reads | First release |
| DR (`dr`) | Published monthly DR or a supported daily accrual source. `excluded_inputs: monthly_distribution_rewards` overrides the placeholder zero | Later; unavailable initially when excluded |
| GAR (`gar`) | Effective-dated program eligibility and validated monthly consolidation dependency | Later; zero/N/A only when demonstrably not applicable |
| Demand-side revenue (`demand`) | Sum all applicable, distinct demand-side components once; include Chronicle Points or disclose them as an additional child | Partial until its expected component set is covered |
| Interest expense (`interest-expense`) | Explicit Sky funding-interest child first; full parent requires all applicable financing costs | First release child, broader parent later |
| Allocation interest (`interest` and Spark children) | Approved instrument/beneficial-share mappings and recognition, split from external rewards, fees and price gains | Obex pilot first; expand by family |
| Protocol/curator fees (`protocol`, `curator`) | Distinct fee-entitlement/revenue sources and beneficiary attribution | Later; not inferred from lending/vault labels |
| Incentives/rewards received (`rewards`) | Distinct issuer/reward streams, recognising income separately from receipt; one statement destination per stream | Expand using existing incentive ledgers |
| Asset gains (`gains`, Spark MORPHO/AVAX/ETH/non-USD reserve children) | Owned quantities, capital flows, valuation policy and realised/unrealised decomposition | Later; product AUM price changes are not prime gains |
| Operating expenses and engineering/legal/security/other children | Approved expense ledger with period, entity, category and evidence | Later |
| Aave licence/profit share (`licence`) | Contractual share and period basis, distinct from supply interest and funding expense | Later |
| Incentive expense (`incentives`, own-token/stablecoin children) | Prime-funded distributions/obligations; not incoming issuer income | Later |
| Distribution costs / impairment | Their own supported expense streams/policies | Later |
| Tax (`tax`) | Explicit tax inputs or confirmed applicability | Later; never silently zero |
| Consolidation adjustments (`eliminations`) | Signed, attributable eliminations at the requested scope | Group/combined phase |
| Subtotals and net income | Backend formulas over complete expected inputs, with partial coverage separately disclosed | Available as dependencies become complete |

Spark's existing allocation hierarchy needs the following initial mapping
inventory. These are candidate source associations, **not blanket approvals** of
their income classification:

| Dashboard family | Candidate stored venue sources / gap |
| --- | --- |
| SparkLend: USDS, USDC, USDT, DAI, PYUSD, RLUSD, WETH | Match configured reserve/token/chain identities. S1–S5 cover the existing USD rows; do not synthesize missing reserves from the UI template. Separate supply yield from protocol fees. |
| Aave: Ethereum/Lido USDS, Ethereum USDC/USDT, Base USDC, Arbitrum USDCn, Avalanche USDC | S6–S9 and matching chain-specific Aave venues; review beneficial shares and rewards. |
| Morpho: Blue Chip USDC/USDT, DAI, USDS, Base USDC, Sentora RLUSD | S10, S11/S65, S12, S13 and matching chain/vault identities. Map redeployments to stable business IDs without duplicating balances or income. |
| Maple syrupUSDC / syrupUSDT | S14/S15; Obex's equivalent single-position source is V1. |
| Arkis | S18; verify share-price recognition and any embedded fees. |
| Anchorage | S23 accrual remains unavailable without confirmed terms. Its known ALM receipts are a cash disclosure, not replacement accrued income. |
| PYUSD / RLUSD issuer income | S68/S67 ledgers. Choose interest versus incentives classification explicitly; do not put the same stream under both the issuer allocation and the top-level rewards row. |

Other primes use the common statement structure with applicable source-specific
children. They must not inherit Spark-only rows or unverified zeroes.

### 4.2 Portfolio positions

| Dashboard column / total | Required reporting API output |
| --- | --- |
| Investment, prime and actual cutoff | Stable economic position ID, source venue references, holder/beneficiary scope and valuation date. |
| Opening / closing value | Existing valuations plus measurement basis and component inclusion metadata. |
| Income earned, accrual basis | Explicit supported recognition per income component. Do not gate solely on the legacy method name `loan_accrual`. |
| Interest receivable / income received in advance | Typed receivable/payable components. Issuer incentives are not automatically *interest* receivable; classify them or label a broader income receivable. |
| Cash income received | Classified income receipts, excluding principal, with source/stream attribution. |
| Interest expense | Venue-level funding cost only after a complete, reconciling allocation policy exists. Prime funding cost alone does not fill every venue cost. |
| Net investment yield | Backend-owned definition, numerator, denominator and annualisation; unavailable until supported. |
| Portfolio aggregate | Approved, non-overlapping tracked-allocation value with included/excluded positions and coverage. Never present it as total entity assets or NAV without that perimeter. |

Proposed later yield definition: `(recognised investment income - allocated
funding interest) / time-weighted funded principal * 365 / elapsed UTC days`.
Return a decimal ratio (`0.075` displays as 7.50%), not a dollar amount. Confirm
the income components and denominator before enabling it. Unknown/zero
denominators produce unavailable yield; losses retain their sign. Annual yields
are recalculated from annual inputs, never summed from monthly percentages.

## 5. Accounting and aggregation rules

### 5.1 Separate the reporting model from settlement math

`settlement.enabled` answers whether a venue contributes to MSC. It does not
answer ownership, consolidation, income classification or whether its position
can be added to another position. `reporting.enabled` means visible, not additive.

Reuse stored results, including prime-level components omitted by v2 reporting,
but apply an explicit, effective-dated reporting policy. For each source record
define its economic position, beneficiary/share, income stream, recognition,
gross/net fee basis, destination line and metric-specific aggregation treatment.
Pricing category or label matching alone is insufficient.

Venue `revenue` may already be net of SDE allocation and include external rewards
or cash adjustments. `actual_revenue` is not universally gross accrued interest
either. Publish approved components and their beneficiary basis; do not rename
the whole number as interest by default.

Inventory non-venue components too: PSM3 sUSDS appreciation, Chronicle Points
and funding reimbursements must not disappear because they have no allocation
row. Classify appreciation separately from funding reductions and reconcile
their treatment without crediting the same amount twice.

### 5.2 Funding expense

The current engine computes:

```text
Sky claim = borrowing interest + SDE revenue - spread reimbursements
net Sky funding-interest component = sky_revenue - sde_revenue
                                  = borrowing interest - spread reimbursements
```

Expose gross borrowing interest, reimbursements and net funding interest as
separate components where saved inputs support them. Reconcile against the
stored daily series. Do not call all `sky_revenue` interest expense: it also
contains SDE income owed to Sky. Do not deduct reimbursements twice.

The first statement may show a child labelled **Sky funding interest, net**.
The parent interest-expense total remains partial if other financing costs are
not covered. Per-venue cost allocation is a later task: reuse audited reporting
logic where appropriate, reconcile allocations plus unallocated cost to the
prime total, and account for off-chain principal and idle/SDE exclusions. Missing
principal must not concentrate all cost into the subset with known balances.

### 5.3 Prevent overlap per metric

| Case | Required treatment |
| --- | --- |
| Spark S23 / S26 | Reuse the existing receipt attribution once. Do not add legacy S26 income to its attributed S23 copy, or use cash when accrual is unknown. |
| Spark PYUSD cash in S28 / accrued issuer income in S68 | One economic income stream: recognise the approved accrual and disclose receipts separately. Deduplicate exact components, not all unrelated income at an ALM address. |
| RLUSD Morpho yield and S67 top-up | Native vault yield and issuer top-up are distinct streams and may legitimately both contribute. Avoid eliminating one merely because they share an asset. |
| Spark savings vaults S56–S60/S63 and backing ALM allocations | Retail product `totalAssets` is a separate managed-product AUM measure, not automatically prime-owned assets. Never add AUM to its backing assets. |
| Grove relay/EOA claims and destination investments | Determine which representation is authoritative at the cutoff; do not add a principal claim to the investment representing the same principal. Include genuine in-flight assets only once. |
| Loan position containing principal plus unpaid income | Do not add its receivable again. Expose composition; a prepaid credit is a liability/advance, not a negative receivable asset. |
| Issuer receivable outside an investment's NAV | Include once if the chosen aggregate includes these claims; disclose this separately from investment principal and cash. |
| Same asset held by different primes | Same token/protocol is not proof of duplication. Use beneficial ownership and actual claims; identify inter-prime holdings/flows before eliminating. |

Aggregation membership must be explicit and versioned. Include an economic
stream once in a statement and a position once in a given portfolio measure.
Every excluded representation needs a reason and, where applicable, a reference
to the included representation. Overlapping/unreviewed sources remain visible
in supporting schedules without silently entering totals.

### 5.4 Subtotals, signs and missing data

Income and expense lines use positive amounts for income earned and expense
incurred, respectively; refunds/losses may be negative. Elimination adjustments
are signed additions to pre-tax income. Sum leaf contributions once:

```text
noninterest income = demand-side + protocol fees + curator fees
                   + classified rewards + investment gains/losses
net interest income = interest income - interest expense
total net revenue = net interest income + noninterest income
noninterest expense = operating + other + incentive expense
                    + distribution costs + impairment
income before tax = total net revenue - noninterest expense
                  + signed consolidation adjustments
net income = income before tax - income tax expense
```

Parent and child values are alternative levels of presentation, not additional
revenue. Unknown expenses/tax do not become zero. Explicit non-applicability or
confirmed inactivity can supply zero with a reason. Missing/excluded components
propagate to their dependent subtotals, not to unrelated known line items.

Allow labelled **known subtotals** for partial coverage, without calling them
the full statement/portfolio total. A complete tracked-allocation sum can be
available even while total legal-entity assets are unknown: its measure and
declared universe must make that distinction clear.

## 6. Proposed read contract

Add a versioned financial-reporting contract. Keep current v2 endpoints, hashes
and `reporting.result.totals: null` compatible. Proposed routes:

```text
GET /v3/reporting/statements?scope=individual&prime=obex&year=2026
GET /v3/reporting/portfolio?scope=individual&prime=obex&month=2026-10
```

Both accept an optional exact `cutoff` and a retained **report revision** for
reproducible exports. `scope=group` requires a prime; `scope=combined` selects
the declared member set without a prime. Unsupported scopes return an explicit
unavailable perimeter, never an individual report relabelled as consolidated.

The statement response includes the line tree, twelve monthly columns, YTD/FY,
per-period actual cutoffs and coverage. The portfolio response includes position
metrics and summary measures. Both include:

- Currency/units, operational versus legal consolidation basis and perimeter ID.
- Reporting policy/taxonomy versions and requested versus actual periods.
- Per-metric availability, recognition, gross/net basis and reasons.
- Source revision/hash references, publication time and freshness.
- Immutable derived report revision/hash and a source-revision manifest.
- Included, excluded and unresolved contributions for aggregates/drill-down.

### Shared metric shape

Illustrative field names, with the observed Obex AR amount:

```json
{
  "line_id": "demand.agent_rate",
  "period": "2026-10",
  "actual_cutoff": "2026-10-26",
  "metric": {
    "value": "70438.27994360848908769110354",
    "known_value": null,
    "unit": "USD",
    "status": "available",
    "recognition": "accrual",
    "basis": "tracked_protocol_entitlement",
    "reason": null,
    "source_refs": ["obex:2026-10-26:agent_rate"]
  }
}
```

The source reference resolves to the immutable input revision/hash in section 2.
For `partial` coverage, `value` is null and an optional `known_value` is shown
only as a labelled known subtotal, with missing/excluded contributors. For
`unavailable` or `future`, both are null. `not_applicable` supplies zero only
under an explicit applicability rule. Available zero and unavailable are distinct.

A partially elapsed month can have an **available** metric through its actual
cutoff. Calendar state (`complete`, `partial`, `future`) and missing-component
coverage are separate concepts. Return reasons such as `source_not_published`,
`classification_unreviewed`, `recognition_unsupported`, `excluded_input`,
`missing_loan_terms`, `overlap_unresolved`, or `scope_not_defined`.

### Period selection

- Closed monthly columns require the exact month-end publication or an explicitly
  approved monthly reporting input, never a partial month substituted as final.
- Without an exact cutoff, the current month may use its latest publication
  within that month, retaining its actual date and freshness. An explicit cutoff
  means that exact completed UTC date; never silently fall back.
- Future months are future, not forecasts. Monthly income is one MTD value at
  the selected cutoff, not the sum of daily MTD snapshots.
- YTD/FY flow totals require every applicable elapsed month. A known October
  cell does not certify January–October. A known covered-period subtotal can be
  disclosed separately. Stock balances use the relevant boundary, never a sum
  of monthly closing balances.
- Combined/group monetary totals require aligned cutoffs, currency, ownership
  scope and accounting basis. Older source dates may be shown individually but
  cannot silently enter a same-date aggregate. Missing members yield partial or
  unavailable totals, with an explicit member list.
- The same alignment rule applies to child metrics within an individual-prime
  subtotal. An October amount through the 25th cannot silently be added to one
  through the 26th as if their coverage were identical.

### Publication and historical coverage

Build reporting documents from stored calculation results, not new RPC/indexer
queries on API requests. Add a reporting aggregation/publication layer after
base calculations, with explicit source and policy revisions. Persist the
numeric documents and small source manifests; reuse existing extraction caches.
An annual document combines selected monthly reporting inputs, not twelve new
blockchain calculations per page load.

Missing business inputs create per-metric gaps. Invalid mappings or failed
aggregation must not publish a misleading total; retain the last successful
report with an explicit stale/failure state. Reporting-document failures must
not change or relabel MSC results. Track reporting publication status separately
from the success of the underlying daily calculation.

Begin with October MTD. Reuse retained, supported old result schemas through
reviewed adapters where their needed inputs exist. The normal worker can only
publish within its rolling 90-day window; an annual endpoint cannot manufacture
January–June coverage by increasing the history query limit. Older months need
an explicit historical reporting publication/import path from approved saved
calculations or reconciled monthly artifacts, with source basis and provenance.
The dashboard still reads only the reporting API, not files/spreadsheets itself.
Do not require a complete compatible v2 venue publication to expose an
independently supported component such as AR from a validated stored result.
Retain its actual cutoff, source basis and stale status; missing venue inputs
must not become a blanket barrier to every financial metric.

## 7. Delivery sequence

Keep the pilot small: a handful of explicit source-to-line mappings, the existing
immutable results, decimal arithmetic and two read responses are sufficient.
No general ledger, universal rule engine, new extraction infrastructure or
complete ownership database is required. Freeze only the IDs/policies needed
for the pilot; return explicit gaps for the rest of the template. Reuse the
existing publication store where practical, or a small versioned document store
for derived reports. Do not build a consolidation framework before exposing AR.

| Slice | Backend work | Visible result / exit condition |
| --- | --- | --- |
| 0 — contract and availability | Freeze taxonomy IDs; define the operational prime scope, metric statuses, policy registry and historical coverage inventory. Check the current publication gaps independently. | Every TBD can have a specific reason. One agreed API fixture covers current-month values, missing prior months and future months. |
| 1 — useful individual-prime figures | Expose AR and typed Sky funding components; approve Obex V1's income measurement and single-position aggregate. Publish monthly/portfolio documents with source references. | October Obex AR, the funding-interest child, investment value and approved Maple income render as actual values; unknown DR/tax/expenses stay unavailable. |
| 1 integration — required companion change | Bind statement values, add supported portfolio summary/recognition fields and update CSV export/validators in reporting-dashboard. | A user sees the new numbers in the actual table and CSV, not only in an API response or hidden supporting schedule. |
| 2 — broader individual coverage | Map Spark/Grove/Osero families; split rewards/fees/gains; classify incentive ledgers; add approved cost allocation and yield; extend historical months. | More complete parents and YTD become available only as dependencies are covered. No requirement to finish all six primes together. |
| 3 — group and combined consolidation | Effective-dated entities/ownership, inter-entity/inter-prime relationships, balances and flow eliminations; approved scope-specific reporting policies. | True consolidated results reconcile from member contributions through explicit adjustments; pre-elimination aggregates remain separately named. |

The minimum first API/adapter PR need not implement group consolidation, venue
cost allocation or yields. Do not make those dependencies block AR or a safe,
single-position tracked aggregate. After the pilot, extend explicit mappings
rather than inferring financial classifications from venue labels.

## 8. Acceptance checks

1. In the dashboard's individual Obex October view, at least the known AR and
   tracked closing allocation value replace TBD, plus the supported funding
   component. Approved Maple income fills its leaf when its policy is enabled.
2. Expense/tax/DR gaps do not hide known siblings. Excluded DR zeroes remain
   unavailable; verified zeroes remain visible zeroes.
3. A fixture with S23/S26 and S28/S68 counts each income stream once. Savings AUM
   plus backing assets, relay plus destination investment, and loan values plus
   included receivables cannot inflate aggregates. Distinct top-up/native-yield
   streams and disjoint holdings of the same token are not incorrectly removed.
4. Funding components satisfy the Sky-claim bridge above. When venue cost
   allocation is enabled in a later slice, all allocations plus unallocated cost
   reconcile to their eligible prime total.
5. An approved aggregate shows its exact members and exclusions. Unreviewed
   positions remain visible; a partial known subtotal never masquerades as total
   portfolio value, consolidated revenue, pre-tax income or net income.
6. Missing January–September data leaves YTD unavailable despite a known October
   MTD cell. Month-end, future-month, inception and leap-year cases are covered.
7. Combined reports cannot combine different dates, inconsistent ownership
   scopes, currencies or gross/net bases without explicit adjustment/coverage.
8. UI and CSV use the same report revision and decimal values. Corrections
   produce new reports; retained reports/exports preserve their original manifest.
9. Existing v2 consumers still validate, settlement numbers remain unchanged,
   and reporting reads make no provider calls or calculation-triggering writes.

## 9. Decisions needed as coverage expands

- Approve the pilot operational perimeter and net-of-embedded-fees investment
  income policy. Legal-entity ownership can follow without renaming operational
  figures as statutory entity statements.
- Decide whether PYUSD/RLUSD issuer compensation belongs under the displayed
  allocation-interest families or noninterest incentives, and the treatment of
  USDG's receipt-basis proxy. Each stream gets one destination.
- Confirm the scope of funding expense, venue allocation policy and the desired
  definition of net investment yield before enabling those additional metrics.
- Provide off-chain terms, expense/tax records and group/elimination inputs for
  their own later slices; they do not block independently supported amounts.
- Prioritise additional historical months once October MTD has visible value.

Related contracts: [v2 dashboard API](pipeline/settlement-reporting-api.md),
[October backfill](pipeline/october-2026-api-backfill.md),
[daily operations](pipeline/revenue_operations.md), and
[monthly finalization](pipeline/monthly-from-daily-revenue.md).
