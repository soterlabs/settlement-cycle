# Grove monthly offchain accruals

`GET /v3/reporting/statements?prime=grove&year=2026` defaults to
`prime-management.offchain-accruals.v3.4`. It adds two income leaves:

- `investment.offchain.gaclo`: January–July supplied history; August–September estimates. October is unavailable.
- `investment.offchain.warehouse`: July–September supplied history; October estimate through **October 25**, with no extrapolation.

Both feed the Offchain lending family, allocation income and net revenue once.
Cash receipts and receivable balances are supplemental measures, never extra
income. Existing settlement documents, MSC and daily financial publications are
unchanged. Income is as supplied; separate fees were not supplied, so these
figures are not certified net of all fees. Existing unknown expense components
remain unknown.

## Reading the response

`offchain_positions[]` contains `source_venue_id` (E21/E42), `label` and `values`
keyed by month. Each month has its own `period_start`, `period_end` and `metrics`:

| Metric | Meaning |
| --- | --- |
| `opening_principal`, `closing_principal` | Supplied funded principal, not NAV |
| `principal_additions`, `principal_returns` | Capital movements, excluded from income |
| `earned_income` | Supplied monthly accrual; identical to the income-statement leaf |
| `cash_received` | Interest cash in the period, not principal returned |
| `opening_receivable`, `closing_receivable` | Supplied unpaid income carry |
| `fees` | Unavailable until separately supplied/confirmed |
| `adjustments`, `valuation_changes` | Explicit supplied reconciliation components |

Money uses USD decimal strings. `quality` is `supported` or `estimate`, separate
from completeness `status`. Supported means supported by the supplied schedule,
not audited or servicing-certified. GACLO receivables are estimates throughout
because the opening carry is source-derived and proposed for review. The
Warehouse schedule awaits servicing confirmation and lacks the original July
invoice. Known cash is supported even when earned income is estimated.

For income, display `value`, or `known_value` for a partial aggregate. An estimate
is a numeric input and is included in available sums; October Warehouse
flows retain `status: partial` and `known_value` because the supplied period
ends before month-end; `quality: estimate`
propagates through subtotals and YTD totals. Show a concise estimate marker and
the metric's own earning cutoff. **Do not infer coverage from the annual period's
`actual_cutoff`**: October's daily source can be later than the Warehouse's Oct25
schedule. Do not sum principal or receivable balances across months. Missing
months are not zero. No future income is projected.

The dashboard shows income in the statement and a collapsed **Offchain
accruals** schedule for principal, earned, received, unpaid and fees. CSV exports
retain income values, quality, earning dates and immutable sources.

## Provenance and updates

Only the two reviewed monthly CSVs from Grove's October30 archive are bundled,
with their exact bytes and SHA256 values. The full archive, spreadsheets,
credentials and unrelated source data are not included. The archive SHA256 is
`69e98ada28f93b2132a6451482c31c8490f4b8be396e054fc32019774e98b0e0`.

Monthly `offchain_revision` identifies a `counterparty_monthly_accrual` entry in
`source_manifest`, including file hashes, limitations and receipt date. The
report's `selection` pins twelve offchain revisions, including missing months,
as well as the existing daily/monthly revisions. Use
`&revision=<selection>` for reproducible exports. Older policies/selections
remain supported unchanged.

Backfill is the bundled monthly read projection: deploying this version makes
all thirteen supplied venue/month records available immediately. No daily
backfill, database migration or RPC work is required. This is a historical
schedule import, not an automated live accrual model. For later periods, obtain
updated monthly servicing schedules, retain the old snapshot, add a reviewed new
snapshot, and advance the active snapshot. Never edit previously published
snapshots. Confirm fees, GACLO opening carry/final notices and Warehouse servicing
records before upgrading those qualifications.
