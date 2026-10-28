# Financial reporting API — first slice

The read-only v3 API derives individual operational-prime management metrics from
immutable saved revenue results. It makes no provider calls. V1 settlement and
v2 venue reporting contracts remain unchanged. This is an incomplete management
report, not an entity income statement, entity assets or NAV.

## Statements

`GET /v3/reporting/statements?prime=obex&year=2026&scope=individual`

Supported primes: grove, spark, obex, keel, skybase, osero. Group and combined
scopes return 422 until consolidation policies are implemented.

The response has 12 `periods`, `lines`, `source_manifest`, `selection`,
`report_hash`, and `source_lag_periods`. Each line has monthly `values` keyed by
YYYY-MM and a backend-computed `total`. Each metric includes decimal-string
`value` (or null), optional partial `known_value`, `status`, `unit`, recognition,
basis, reason and source paths. Never replace null with zero or display a partial
known value as a complete total.

| Line ID | Measurement |
| --- | --- |
| `demand.agent_rate` | Saved accrued agent-rate protocol entitlement |
| `funding.sky.net` | `sky_revenue - sde_revenue` |
| `funding.sky.gross` | Net funding plus saved sUSDS spread reimbursement |
| `funding.sky.reimbursement` | Saved reimbursement, a deduction from gross |
| `investment.maple_syrup_usdc` | Obex V1 revenue minus external rewards, after embedded vault fees; unsupported for other primes |

These are components. Gross and net funding are alternative presentations, not
additive expenses. Parent interest expense, revenue, DR, tax and net income
remain unavailable. Obex's approved income is explicitly *net investment income*,
not gross interest. DR excluded by the saved pipeline is never a certified zero.

Closed months require an exact month-end publication. The current month uses the
latest saved publication within that month, exposes its actual cutoff and marks
staleness. Missing prior months keep YTD null; `known_value` may disclose the
known portion. Future months have `status: future`, not forecast zeroes. Monthly
MTD snapshots are never added together within a month.

An optional `cutoff=YYYY-MM-DD` selects an exact completed UTC day in the selected
year. With no cutoff, the current expected date is yesterday. `source_lag_periods`
identifies selected dates where a newer raw source awaits financial publication.

Use the returned opaque `selection` as `revision` to retain the exact annual
report: `GET /v3/reporting/statements?prime=obex&year=2026&revision=TOKEN`.
It freezes all 12 selected financial revisions, including missing slots. It is
an encoded selection, not an authentication token. Do not construct it in clients.
`revision` and `cutoff` are mutually exclusive. The `report_hash` covers the
stable statement document, excluding itself and dynamic `source_lag_periods`.
The HTTP ETag covers the entire response. Keep the selection in CSV download
links so corrections do not silently change the displayed report's export.

## Portfolio

`GET /v3/reporting/portfolio?prime=obex&month=2026-10&scope=individual`

`portfolio.measure` is `tracked_allocations`. The pilot perimeter
`obex.maple-only.v1` contains exactly `obex.maple_syrup_usdc` (source venue V1).
Opening/closing values use saved USD vault valuations. The response lists
positions, expected members and excluded source venue IDs. Additional unreviewed
Obex positions make the aggregate partial (null `value`, separate `known_value`).
Other primes' aggregates remain unavailable.

Pin a retained `financial_revision` using `revision`, or match a v2 venue report
using `source_revision` and its exact `cutoff`. These selectors are mutually
exclusive. The returned source revision/hash, policy version, computed timestamp,
financial document/hash and freshness support reconciliation. `report_hash`
hashes `financial_document`; dynamic freshness is outside that hash.

No publication returns 404; invalid scope/selection returns 422; a store outage
returns 503. Responses support conditional GET with ETags.

## Publication and backfill

Policy: `prime-management.first-slice.v1`. A changed measurement policy requires
a new version. Financial revisions key the source revision/hash plus policy;
corrections append new documents and retain previous ones.

The existing daily revenue worker publishes financial documents after each
successful or reused source result. Derived failures have a separate attempt
ledger and fail the run without marking an already committed source result
failed. The next run or financial backfill can retry.

Backfill retained results without RPC, price or rate requests:

```sh
python -m settle.revenue.financial_backfill --from 2026-01-01 --to 2026-10-27
```

Optional repeated `--prime` restricts the run. It applies the additive schema,
selects the latest saved source revision per date, and prints per-prime counts
of source dates, published, reused and failed. Reruns are idempotent. A zero
source-date count means missing retained history, not completed coverage. This
command cannot recover dates absent from the raw store; use the existing source
backfill separately when that work is intended. The daily worker remains at its existing schedule. A separate hourly
financial reconciliation job can run the same command with `--to` omitted
(default: yesterday) to pick up older source jobs/corrections and retry derived
failures without rerunning upstream calculations. Deploy the same reviewed
policy code to the API, daily worker and financial reconciliation service when
changing the financial policy.
