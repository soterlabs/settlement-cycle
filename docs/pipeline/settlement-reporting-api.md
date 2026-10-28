# Settlement and reporting dashboard API

This guide describes the v2 API introduced by the independent venue views change.
It becomes available after deployment and publication of new daily results. It
does not imply that an existing production deployment has been upgraded.

## Choose the view

| Dashboard | API view | Income meaning | Used for MSC? |
| --- | --- | --- | --- |
| Settlement | `settlement` | Existing MSC recognition, including cash receipts where configured | Yes |
| Position / accrual reporting | `reporting` | Venue-specific reporting method; can differ from settlement | No |

A venue has one identity and two independently configured views. `existing`
reuses its established valuation/revenue calculation. It does **not** mean every
settlement venue is cash-based, or every reporting venue is accrual-based. Vault
appreciation, position-only savings holdings, and cash distributions retain their
established behavior unless a method override is configured.

Both views are calculated together and saved in one immutable daily result.
Requests read that result; they do not call providers or refresh calculations.

## Endpoints

All dates are ISO `YYYY-MM-DD`, all cutoffs are completed UTC days, and all amounts
are USD decimal **strings** or `null`. Use decimal arithmetic, not binary floating
point, for financial calculations.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v2/venues/{prime}` | Current configuration, including both enabled flags and methods |
| GET | `/v2/settlement/{prime}/latest` | Latest published settlement view |
| GET | `/v2/reporting/{prime}/latest` | Latest published reporting view |
| GET | `/v2/{view}/{prime}/at/{cutoff}?revision={revision_id}` | Historical view; optional exact revision |
| GET | `/v2/{view}/{prime}/history?start=...&end=...&limit=90` | Latest revision per cutoff, newest first |
| GET | `/v1/revenue/{prime}/revisions/{cutoff}?limit=100` | Retained revision metadata |
| GET | `/v1/revenue/status` | Shared daily publication monitor |

`view` is `settlement` or `reporting`. Supported primes are `spark`, `grove`,
`obex`, `keel`, `osero`, and `skybase`. A history window contains at most 90
calendar days; `limit` is 1–90. Split longer ranges into successive date windows.
The normal worker publishes within the most recent 90 completed UTC days;
older retained results remain readable. Adding v2 views does not backfill old
publications automatically.
The result cadence is daily but each income value is **month to date**, not that
day's income. Position values are balances at the cutoff.

The inventory endpoint describes **current configuration**, not the
configuration of an old revision. Render historical tables from the saved view
rows; do not reclassify them using today's inventory.

## Read both views at the same revision

Fetch settlement first and then pin reporting to its cutoff and revision. This
avoids mixing publications if a new result arrives between requests.

```javascript
const base = API_BASE_URL;
async function get(path) {
  const response = await fetch(base + path);
  if (!response.ok) throw new Error(`API ${response.status}: ${await response.text()}`);
  return response.json();
}

const settlement = await get('/v2/settlement/spark/latest');
const { cutoff, revision_id } = settlement.data;
const reporting = await get(
  `/v2/reporting/spark/at/${cutoff}?revision=${revision_id}`
);

const msc = settlement.data.result.totals;
const positions = reporting.data.result.venues;
// Check freshness, latest_attempt and per-row status before displaying values.
```

`schema_version` is `2.0`. Latest and historical-cutoff responses contain:

| Field | Meaning |
| --- | --- |
| `prime`, `view` | Selected prime and accounting view |
| `cadence` | `daily` |
| `estimate_basis` | `month_to_date` |
| `data.cutoff` | Inclusive UTC cutoff |
| `data.revision_id` | Shared immutable calculation identity |
| `data.computed_at` | Publication computation timestamp |
| `data.provisional` | Daily estimates remain provisional, including at month-end |
| `data.excluded_inputs` | Inputs omitted from the daily estimate, e.g. monthly distribution rewards |
| `data.code_version`, `data.configuration_version`, `data.input_revision` | Calculation/input versions |
| `data.opening_pins`, `data.closing_pins` | Shared chain block boundaries |
| `data.source_schema_version` | Underlying stored result schema (`1.2` for this change) |
| `data.source_result_hash` | Hash of the complete original result, available through the v1 revenue endpoint |
| `data.view_hash` | Hash of this projected `data.result`; differs between views |
| `data.result.period` | Inclusive start/end dates and closing pins |
| `data.result.venues` | Frozen venue rows, including disabled/unavailable venues |
| `data.result.complete` | False if any row has status `unavailable` |
| `data.result.totals` | MSC totals for settlement; `null` for reporting |
| `freshness` | Expected cutoff, actual cutoff, and `stale` flag |
| `latest_attempt` | Most recent worker attempt, which may have failed after this successful publication |

For an explicit historical cutoff, freshness compares with that requested date.
For `latest`, it compares with yesterday UTC. A fresh publication can still have
an unavailable reporting row: freshness and completeness are separate checks.

## Venue row contract

| Field | Meaning |
| --- | --- |
| `venue_id`, `label`, `chain` | Stable identity and descriptive fields |
| `enabled` | Inclusion in the selected view |
| `method` | `existing`, settlement `alm_receipts`, or reporting `loan_accrual` |
| `status` | `available`, `disabled`, `not_started`, or `unavailable` |
| `reason` | Explanation when unavailable, disabled, or not started |
| `opening_position_value_usd` | Position immediately before the period starts |
| `position_value_usd` | Position at the cutoff |
| `income_usd` | Income recognized in this view during the period |
| `cash_received_usd` | Classified cash income received during the period, net of explicit refunds, where supported |
| `principal_usd` | Outstanding contractual principal, where supported |
| `opening_unpaid_income_usd` | Unpaid income before the period starts, where supported |
| `unpaid_income_usd` | Unpaid income at the cutoff, where supported; negative means prepaid |

`null` means unavailable or not measured by that method. It must never be turned
into zero. `available` means the method's primary position/income calculation is
available; it does not promise every optional metric is measured. An `existing`
vault row may have valid position and income with `cash_received_usd: null`.

An unavailable loan row can still carry a known `cash_received_usd`. Display it
as known cash alongside an unavailable accrual valuation, without substituting
cash for accrued income. Disabled/not-started rows have null financial metrics.

`existing` income retains the original venue revenue definition, including any
SDE allocation; it is not automatically whole-venue gross yield. Existing savings
vaults are position-only and their computed income remains zero.

### Anchorage attribution

S23 represents the Anchorage loan; S26 represents USDC held in Spark's ALM.
The underlying MSC calculation still recognizes Anchorage sweeps through S26.
The new API views attribute those classified receipts to S23 and deduct the same
amount from S26's displayed income, so settlement venue income is counted once.

S23 includes `receipt_attribution: { source_venue: "S26", payer: "0x..." }`.
S26 includes `income_attributed_to: ["S23"]` and
`income_before_attribution_usd` for reconciliation. This changes attribution,
not MSC totals. S26's actual cash position remains its measured balance.

Classification reuses the exact MSC principal-return and yield-refund overrides,
including partial principal/interest splits. It never treats an entire loan
repayment as interest. Unresolved splits retain their current conservative MSC
treatment and require confirmation before reliable loan reporting.

In the checked-in configuration, S23 reporting is enabled with `loan_accrual`,
but its valuation/income is **unavailable pending confirmed terms**. Approximate
APR and principal comments in the repo are not approved contractual inputs.
Settlement receipts remain available. Other venues default to `existing`.

### Loan accrual example (fictional values)

Suppose March 1–3 earns $30, receives $100, starts with $180 unpaid interest,
and has $36,500 principal. The reporting row includes:

```json
{
  "venue_id": "S23",
  "method": "loan_accrual",
  "status": "available",
  "income_usd": "30.0",
  "cash_received_usd": "100",
  "principal_usd": "36500",
  "opening_unpaid_income_usd": "180.0",
  "unpaid_income_usd": "110.0",
  "position_value_usd": "36610.0"
}
```

The settlement row recognizes $100. Reporting recognizes $30. They are two
views of the same income stream and must not be added together.

Available loan rows also include `daily` (period-only principal, income, cash,
and unpaid balances), `terms_source`, `day_count`, and `terms_confirmed_through`.
The reconciliation is:

```text
closing unpaid income = opening unpaid income + period income - cash income received
closing position = outstanding principal + closing unpaid income
```

Daily interest uses EOD principal and simple, net contractual APR. Principal and
APR schedule changes take effect on the specified UTC date; ACT/365 uses 365
even in leap years. ACT/360 is also supported. No compounding is inferred.

## Dashboard totals and charts

For MSC headline cards, use `settlement.data.result.totals`, particularly:

```text
prime_agent_total_revenue = prime_agent_revenue + agent_rate
                          + distribution_rewards + chronicle_points + gar
monthly_pnl = prime_agent_total_revenue - sky_revenue
```

Venue income alone can differ from the MSC headline because prime-level income
includes Curve/PSM3 adjustments and demand-side rewards. The settlement totals
expose these components. Preserve the published decimal strings.

Reporting deliberately has **no consolidated total**. Savings vaults can overlap
their underlying allocation holdings. Likewise cash recognized in an existing
ALM row can correspond to accrued income in a separate incentive row (for example
Spark S28 and S68). Build explicitly scoped metrics and label their basis; do not
sum every reporting position or income row into an alleged consolidated NAV or
revenue figure. The S23/S26 attribution described above is handled explicitly.

For a daily income chart within one month, subtract consecutive MTD income
values for the same venue/view. At a month boundary, start from the new month's
MTD value. If dates are missing, the difference spans the entire gap, not one
day. Revisions can change prior MTD amounts: pin a consistent revision selection
and preserve the identifiers in exports. Use position values directly for
balance charts; do not cumulatively sum them.

## Errors, caching, and historical compatibility

| Status | Meaning | UI treatment |
| --- | --- | --- |
| 200 | Stored view returned; inspect row status, completeness and freshness | Render known values with appropriate labels |
| 304 | ETag unchanged | Reuse cached document |
| 404 | Unknown prime or no publication for selection | Missing data, not zero |
| 409 | Selected revision predates stored independent views | Use compatible v1 data where appropriate or publish a new calculation |
| 422 | Invalid date, view, revision syntax, or history window | Fix request |
| 503 | Store unavailable | Retain last successful display and show failure |

Results use the existing ETag and public `max-age=300` contract. Failed refreshes
retain the previous successful result. Check `latest_attempt` and freshness to
show this clearly. `/v1/revenue/status` monitors publication success, not whether
all contractual reporting inputs have been supplied.

History returns an `availability` per published date. Old incompatible
revisions have `availability: "unavailable"`, their identity and a reason,
without fabricated view values. Missing dates are omitted. Latest/at requests
for incompatible revisions return 409. No API request rewrites saved records.

The original `/v1/revenue/...` endpoints retain the original result/hash. New
results have stored schema `1.2` with `venue_views`; older result schemas retain
their original labels and hashes. Legacy venue config flags remain readable,
but new configuration uses the two blocks below.

## Configuration and extending accrual reporting

Every configured venue declares independent blocks:

```yaml
settlement:
  enabled: true
  method: existing
reporting:
  enabled: true
  method: existing
```

Reporting-only holdings use `settlement.enabled: false`. Reporting can be
disabled independently. `skip` remains an input/calculation constraint; enabled
does not override a skipped or unpriceable venue.

An off-chain loan can attribute existing ALM recognition and override reporting:

```yaml
settlement:
  enabled: true
  method: alm_receipts
  source_venue: S26
  payer: '0x49506c3aa028693458d6ee816b2ec28522946872'
reporting:
  enabled: true
  method: loan_accrual
  pending_reason: 'Awaiting confirmed contractual inputs'
```

Once the terms are confirmed, replace `pending_reason` with `loan_terms`:

```yaml
loan_terms:  # Fictional example; do not use as Anchorage's actual terms.
  opening_date: '2026-02-01'
  opening_unpaid_interest_usd: '0'
  confirmed_through: '2026-03-31'
  day_count: act/365
  source: 'Reference to approved agreement and reconciled principal/receipt history'
  tranches:
    - id: facility-a
      principal_schedule:
        - date: '2026-02-01'
          principal_usd: '36500'
        - date: '2026-03-15'
          principal_usd: '18250'
      apr_schedule:
        - date: '2026-02-01'
          apr: '0.10'
```

Schedules contain absolute EOD principal balances, not cash flow amounts. A
zero principal entry ends accrual for a repaid tranche. Separate tranches support
different rates. Supply net APR after contractual fees; the calculator does not
infer a fee structure. Opening unpaid interest is immediately before
`opening_date`; classified receipts from that day onward reduce unpaid income.
The opening date must be covered by the prime's receipt history.

`confirmed_through` explicitly limits verified coverage: reporting becomes
unavailable after it, rather than assuming the principal, rates and receipt
classification remain correct indefinitely. Updating reporting terms does not
alter the MSC calculation. New results record the new configuration version;
previous publications remain immutable.

The initial `alm_receipts` method attributes a unique payer from an enabled,
same-token, same-chain Cat A settlement anchor. It preserves the established
MSC recognition path; it is not a new way to include or exclude that payer's
income from MSC. Additional accounting models should be separate methods with
their own input validation and reconciliation.


## Financial dashboard aggregates (v3)

For typed management-reporting statement components, tracked portfolio aggregates,
coverage and retained CSV selections, see the [financial reporting API guide](financial-reporting-api.md).
V2 venue `totals` remain null; v3 does not change settlement amounts or recognition.
