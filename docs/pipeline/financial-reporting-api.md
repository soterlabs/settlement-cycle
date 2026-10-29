# Financial reporting API — contract and policies

The read-only v3 API derives individual operational-prime management metrics from
immutable saved revenue results and pinned published monthly settlement summaries.
It makes no provider calls. V1 settlement and
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
| `demand.agent_rate` | Published monthly agent rate; otherwise saved MTD accrued entitlement |
| `demand.distribution_rewards` | Published monthly distribution rewards |
| `demand.total` | Published demand-side headline, including applicable GAR/Chronicle Points |
| `demand.gar` | Published governance accessibility rewards; Skybase only |
| `funding.sky.net` | `sky_revenue - sde_revenue` |
| `funding.sky.gross` | Net funding plus saved sUSDS spread reimbursement |
| `funding.sky.reimbursement` | Saved reimbursement, a deduction from gross |
| `investment.maple_syrup_usdc` | Obex V1 revenue minus external rewards, after embedded vault fees; unsupported for other primes |

These are components. Gross and net funding are alternative presentations, not
additive expenses. Parent interest expense, total revenue, tax and net income
remain unavailable. DR and demand-side revenue require a published monthly summary;
missing or unfinalized months remain unavailable. Obex's approved income is explicitly *net investment income*,
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
It freezes all 12 selected financial revisions and, under the monthly statement
policy, all 12 monthly summary revisions, including missing slots in both sources. It is
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

Current daily/portfolio policy: `prime-management.allocations.v2.1`;
current annual statement composition policy: `prime-management.monthly-statements.v3`; retained allocation policy: `prime-management.allocations.v2`; retained first-slice policy: `prime-management.first-slice.v1`. A changed measurement policy requires
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

## Initial production backfill — 2026-10-28

The January 1–October 27 request published **335** financial documents with no
failures, using every retained source date in that window:

| Prime | Source dates | First cutoff | Latest cutoff |
| --- | ---: | --- | --- |
| Grove | 56 | 2026-09-01 | 2026-10-26 |
| Spark | 55 | 2026-09-01 | 2026-10-25 |
| Obex | 56 | 2026-09-01 | 2026-10-26 |
| Keel | 56 | 2026-09-01 | 2026-10-26 |
| Skybase | 56 | 2026-09-01 | 2026-10-26 |
| Osero | 56 | 2026-09-01 | 2026-10-26 |

All six annual responses have the September 30 publication and October MTD.
January–August have no retained source results, so YTD remains unavailable.
Later October cutoffs will follow source publication; this financial backfill
does not assert that missing raw dates were computed.

The Railway `settle-reporting-financial` service runs hourly at :35 UTC with
`--from 2026-01-01` and a rolling yesterday endpoint. Its initial deployment is
an explicit artifact from this PR so backfill can run before merge; the IaC
service definition uses main for subsequent managed deployments. API and
companion dashboard PR commits were deployed without merging the PRs.

Initial job deployment: `4f4e5013-4a79-4324-b4ae-16ad889b75c4`.
Initial successful run: `56d3cf71-db4a-4cfd-ac3a-f67990b1554c`.

Live verification matched Obex's statement cells, portfolio aggregate and pinned
CSV to the saved source: AR $70,438.28; net Sky funding $1,122,790.65;
Maple net investment income $1,460,382.34; tracked closing allocation value
$406,996,930.93. These values are October 1–26 MTD, not a complete month or YTD.


## Allocation coverage extension

The [allocation policy guide](financial-allocation-reporting.md) documents the broader
Spark/Grove/Osero mappings, separate reward recognition, issuer claims, exact
portfolio membership and backwards-compatible policy upgrade.


## Published monthly history (monthly-statements.v3)

The API package includes 48 immutable `summary.md` sources from
[soterlabs/settlement-reports at 66854e8](https://github.com/soterlabs/settlement-reports/tree/66854e865be5d6972a975a5069de56a3c83c2704/reports).
This initial slice covers January–September 2026 for Spark, Grove, Obex, Keel
and Skybase, and July–September for Osero. There are no Osero January–June
summaries in this snapshot; those cells remain unavailable. Skybase GAR is
available only where an explicit GAR row exists (January–July in this snapshot);
its absence in August/September is not treated as a published zero.

For each closed month, its published headline supplies DR, demand-side revenue,
AR and net Sky funding (`prime cost of funds`, excluding Sky direct exposure).
Grove's explicitly published Chronicle Points and Skybase's explicitly published
GAR are included where present. Source amounts are rounded to cents, denominated
in USDS and displayed as USD at par; this conversion and precision are disclosed
in the manifest. The published total is retained exactly, allowing only the
rounding difference between independently rounded components. Supply-side
settlement profit and per-venue settlement revenue are **not** reclassified as
accrued investment income. Gross funding and reimbursement are not inferred from
missing headline fields.

Monthly headline fields override corresponding month-end daily fields; other
approved daily allocation metrics remain intact. Open-month MTD metrics continue
using existing publications. DR and demand-side MTD remain unavailable until a
closed monthly summary is included. Historical detailed allocation figures remain
unavailable when there is no approved daily financial publication. No daily
source backfill, rate request, database migration or recomputation is needed for
the included monthly headline history. This adds no new daily processing; it
does not remove the existing current-month daily pipeline.

Periods add `monthly_revision` alongside `financial_revision`. A January period
can therefore have a monthly revision and an actual month-end cutoff with no
daily financial revision. Each available monthly metric has a `source_ref` into
`source_manifest`, identifying whether that particular cell uses an immutable
monthly summary or a daily financial publication. Monthly manifest entries have
`kind: published_monthly_summary`, repository, full commit, path, Git blob SHA,
SHA-256 `result_hash`, cutoff, currency, precision and source URL. Saved report
selections pin both source sets; old policies/selections remain unchanged.
Portfolio and settlement endpoints are unaffected.

### Adding or correcting monthly sources

This slice intentionally uses a reviewed bundled snapshot; it does not poll GitHub
at request time or automatically follow mutable main. To capture a new source
commit, use an authenticated GitHub CLI session and run:

```sh
python scripts/snapshot_monthly_reports.py --commit FULL_40_HEX_SHA \
  --output src/settle/revenue/published_monthly/FULL_40_HEX_SHA.json
```

Append its SHA to `monthly_reports.SNAPSHOTS` (oldest to newest), retaining every
previous snapshot so pinned exports remain replayable. The newest snapshot
selects the active prime/month heads. All source bytes, period identities,
demand components, precision and rounding bridges are validated on load. Test
`tests/unit/test_monthly_reports.py`, review changed values and deploy the API
through the normal reviewed release. No database import job is needed. Merely
opening this PR does not publish these figures to the live API.
