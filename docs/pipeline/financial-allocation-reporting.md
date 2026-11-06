# Broader individual-prime reporting — policy v2

This is slice 2A of the [financial reporting PRD](../PRD_reporting_dashboard_financial_api.md).
It extends the existing v3 API and dashboard with reviewed native lending yield,
net vault income, receipt-based lending rewards and typed issuer compensation.
It uses immutable saved results; it does not run extractors or change MSC.

## Coverage and measurement

The exact membership and stable business-line IDs live in
[`financial_allocations.py`](../../src/settle/revenue/financial_allocations.py).
The policy is `prime-management.allocations.v2`, reviewed from September 1, 2026.
Earlier allocation cells are unavailable until their history is reviewed; existing
AR, Sky funding and the original Obex mapping retain their previous coverage.

| Prime | Reviewed investment sources | Additional components |
| --- | --- | --- |
| Spark | S1–S5 SparkLend; S6–S9/S35/S41/S54 Aave; S10–S13/S65/S34 Morpho; S14/S15 Maple; S18 Arkis; S16 Ethena; S17/S36/S42 Fluid | External lending-token distributions separately; S67/S68 issuer accrual and S69 USDG receipts when complete saved ledgers exist |
| Grove | E1–E3 Aave; E4–E6/E19/E23/E24/E25 Morpho; E37 Maple | External lending-token distributions; Chronicle Points entitlement |
| Osero | O1 SparkLend USDS | External lending-token distributions; disjoint O2 idle USDS in the tracked value perimeter |
| Obex | Existing V1 Maple policy | Previous values remain available |
| Keel / Skybase | No newly reviewed investment positions | Existing AR and funding components remain available |

Each source ID is an explicit reviewed identity from the repository configuration,
not a label/category inference. Category and zero SDE share are validation guards.
A change to a source ID's economic identity requires a new policy review/version.

For these specified non-SDE lending/vault positions:

```text
native investment income = saved venue revenue - saved external_revenue
external lending distributions = saved external_revenue, recognised on receipt
```

The vault component is after embedded fees. It is not gross interest income.
Native yield and external distributions are separate streams; neither is added
twice. The business line `investment.morpho.blue_chip_usdt` sums S11 and S65,
which are two distinct vault addresses with separate balances. Each remains a
separate portfolio member. A missing deployment is unavailable, not assumed zero.

`investment.reviewed_income` is a subtotal of exactly the reviewed native sources.
`rewards.reviewed_lending` is a subtotal of reviewed receipt-based distributions.
Their `aggregation: subtotal` metadata and labels distinguish them from member
leaves; **never add a subtotal to its members**. These are not full prime revenue,
full allocation interest, net income, or full incentives totals. Annual totals keep
their own recognition basis and still require every elapsed month.

## Issuer compensation

Issuer policy is explicit in
[`financial_issuers.py`](../../src/settle/revenue/financial_issuers.py).
RLUSD/PYUSD are separate issuer-incentive accrual components under rewards, not
native pool interest. USDG uses the verified 8-hour-payment receipt proxy.
Neither raw S28 PYUSD receipts nor the ALM's underlying token balance is added as
an additional income stream.

To publish an issuer metric, the saved ledger must cover the exact month-to-date
period/cutoff, identify the correct program and reviewed payer/recipient/token/chain,
match the saved venue amounts and zero SDE share, and reconcile opening balance
plus accrual minus receipts to closing balance. Only sub-picodollar (1e-12 USD)
rounding residuals from the upstream decimal calculation are accepted. Missing or
unreconciled ledgers stay unavailable; a raw cash receipt is not substituted for
RLUSD/PYUSD accrual.

Portfolio responses optionally include `claims`: income, attributed receipts,
issuer-compensation receivable and amounts received in advance. A negative signed
balance becomes a positive advance receipt, not negative interest receivable.
`included_in_allocation_value` is false. Claims are not added to the underlying
holdings or the reviewed investment subtotal. Their detailed source is retained
inside `financial_document`, covered by its report hash.

## Portfolio perimeter and availability

Portfolio positions include an income metric and independent opening/closing
valuation metrics. Missing income does not hide a known valuation and vice versa.
Each v2 member has a distinct `prime.source_venue_id` position ID. Osero's O1 and
O2 are disjoint investments/cash and can form a complete tracked aggregate when
both are available. O2 does not acquire a fabricated zero income component.

Spark/Grove have additional unreviewed holdings. Their aggregate `value` stays
null, with reviewed valuations disclosed as a `known_value` and exact excluded
source IDs. Savings AUM, backing holdings, cash-receipt aliases, relay balances,
RWA positions and issuer claims are not blindly combined. The original Obex
single-position perimeter is preserved. None of these measures is entity assets
or NAV.

Source duplicates, unsupported SDE ownership, disabled/unavailable reporting views
and missing inputs keep dependent values unavailable. A later source revision
produces a new report. No missing input becomes a verified zero.

## Dashboard integration

The dashboard accepts both supported policy versions. V2 line metadata supplies
family and leaf/subtotal classification. Existing Spark reserve rows are bound
explicitly; additional families and other primes use their own applicable rows.
Unreviewed reserves and full parent totals remain TBD. CSV uses the same catalog.

Approved v3 positions can render/export when a saved legacy source has no v2
venue publication, using the v3 source revision and declared members. This does
not fabricate a v2 view hash, cash receipt, loan receivable or missing member.
Native income is recognised independently of external-reward receipts. USDG
receipts cannot fill an accrual-only portfolio column. Both statement and
portfolio exports retain their selected financial/source revisions.

## Upgrade and backfill

The current policy changes the identity of new derived publications. V1 documents
and annual selections remain readable with their original taxonomy, amounts,
policy and hash. Explicit portfolio revision requests can read either supported
policy; default/source-revision requests select the current policy. A policy
upgrade must not rewrite retained reports or add new rows to their frozen CSV.

Deploy the dashboard that accepts both policies first. Publish the new policy
from retained records with the existing financial backfill command, then deploy
the API that selects it by default. Update hourly reconciliation to the same
policy. Existing v1 service consumers remain valid during the transition.

```sh
python -m settle.revenue.financial_backfill --from 2026-01-01
```

Only dates with retained source data are produced. Gaps and incomplete YTD remain
explicit. Financial backfill requires no provider credentials or upstream calls.

## Remaining slice 2 work

RWA interest versus NAV changes, off-chain accrual terms, full coverage of new
Spark reserves/vaults, a complete reporting-only incentive history, approved
funding-cost allocation and net investment yield remain separate follow-ups.
Tax, statutory/entity scope and consolidation are not inferred. This delivery
expands useful independently supported figures without claiming those gaps closed.

## Initial production verification — 2026-10-28

The new policy was deployed to the dashboard, published from saved inputs, then
selected by the API. Backfill created **336** financial documents with zero
failures; a second run reused all 336 with zero new publications/failures.

| Prime | Retained source dates | Latest cutoff |
| --- | ---: | --- |
| Grove | 57 | 2026-10-27 |
| Spark | 55 | 2026-10-25 |
| Obex | 56 | 2026-10-26 |
| Keel | 56 | 2026-10-26 |
| Skybase | 56 | 2026-10-26 |
| Osero | 56 | 2026-10-26 |

All coverage begins September 1. Older source gaps keep YTD unavailable. Hourly
reconciliation remains at :35 UTC and picks up later source publications.

Verified on the live dashboard and pinned CSV exports:

| Prime | Reviewed native income MTD | Lending rewards received MTD | Approved financial members |
| --- | ---: | ---: | ---: |
| Spark | $5,074,953.57 | $239,704.66 | 25 |
| Grove | $312,476.93 | $0.00 | 11 |
| Osero | $5,205.25 | $0.00 | 2, including idle cash |

Spark/Grove disclose reviewed closing values as partial known amounts
($2,465,648,473.76 / $10,421,631.76), not full prime asset totals. Osero's complete
declared investment/cash perimeter closes at $0.68. These are reported valuations
with the cutoffs above; they exclude entity assets outside the declared perimeter.

All six pre-upgrade annual selection tokens were replayed after switching the
policy: their original lines, selections and report hashes were unchanged. The
retained first-slice Obex portfolio hash also matched. The old Spark source's
missing v2 view did not prevent its 25 approved v3 members from rendering and
exporting with the matching financial source references.

Backend PR #230 is stacked on the still-open first-slice PR #229. Companion
dashboard PR #2 and the API commits were deployed for live verification; these
PRs remain open for review.


## Review corrections — policy v2.1

Current publication policy is `prime-management.allocations.v2.1`. It preserves
v2's measurements and taxonomy while correcting issuer source paths for accepted
legacy `display_only_breakdown` records. Published v2 documents and selections
retain their original provenance/hash; default backfill publishes separate v2.1
revisions. V1 and v2 remain supported for retained revision requests.

The dashboard accepts all three policies before the API switches defaults.
Positions CSV rows use the member's actual cutoff even without a v2 venue
publication, and append `Source venue ID` / `Position ID` columns. Identical
business labels across the S11/S65 vault deployments remain distinguishable.
Summary records leave member identity fields empty. Financial members' IDs are
also visible on the positions screen. Legacy venue rows retain source venue IDs
without inventing unapproved economic-position IDs.
