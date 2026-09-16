You are working in `soterlabs/msc-dashboard`. Add daily prime revenue estimates
alongside the existing static monthly settlement numbers. Inspect the actual
code and data-loading paths first; preserve the existing settled-data behavior.
Implement this in a dedicated PR with tests, review it, and report the PR URL.

Backend: `soterlabs/settlement-cycle`, PRD `docs/PRD_prime_revenue_as_of.md`, steps
3–7. The revenue pipeline runs once daily at 20:17 UTC, calculating month-to-date
through the previous completed UTC day. It is not an hourly or intraday feed.
API base URL: https://settle-api-production.up.railway.app
Make the base URL configurable using this dashboard's existing configuration
conventions. The API is public/read-only; no RPC, HyperSync or DB credentials
belong in the dashboard. Fetching an endpoint never starts a calculation.

API contract (schema_version 1.0):
- GET /v1/revenue/{prime}/latest
- GET /v1/revenue/{prime}/at/{YYYY-MM-DD}?revision={64-character revision_id}
- GET /v1/revenue/{prime}/history?start=YYYY-MM-DD&end=YYYY-MM-DD&limit=90
- GET /v1/revenue/{prime}/revisions/{YYYY-MM-DD}?limit=100
- GET /v1/revenue/status (200 when daily results are current and workers healthy;
  503 with per-prime freshness/attempt details otherwise).
Primes: grove, spark, obex, keel, skybase, osero. History windows are at most
90 days, newest cutoff first, with the newest published revision per cutoff.
Missing dates are gaps, not zero revenue. Older retained revisions can be read
explicitly; monthly static reports remain the source for older history.

`latest` and `at` responses contain:
- schema_version, prime, cadence="daily", estimate_basis="month_to_date"
- data: revision_id, prime, cutoff, opening_pins, closing_pins, code_version,
  configuration_version, input_revision, computed_at, provisional=true,
  excluded_inputs (currently monthly_distribution_rewards), result
- freshness: expected_cutoff, actual_cutoff, stale
- latest_attempt: null or {attempt_id, cutoff, status, started_at, finished_at,
  error_type, revision_id}. This may report a failed refresh while data retains
  the last successful estimate. It is the latest attempt for that prime, not
  necessarily the requested historical cutoff.
The result is the full MonthlyPnL object: month, period, pin_blocks_som,
venue_breakdown, sky_revenue, agent_rate, prime_agent_revenue, monthly_pnl,
other revenue components and daily breakdowns. Monetary Decimal values are
JSON strings to preserve precision. Use the existing display conventions and
an appropriate decimal representation for arithmetic. Confirm the mapping of
backend fields to existing dashboard labels—prime_agent_revenue and monthly_pnl
are different metrics. Do not relabel one as the other.

Product requirements:
1. Preserve canonical settled monthly figures and existing static fallbacks.
   Display daily data as a clearly labelled "Provisional MTD estimate through
   YYYY-MM-DD (UTC)" with last computation time and freshness state. Daily
   month-end estimates are still provisional, not canonical settled reports.
2. Match daily estimates to the selected month using the returned cutoff. On
   the first day of a month, latest may still belong to the prior month; do not
   present it as the new month's earnings or silently replace a settled month.
3. Do not sum MTD observations. Differences between estimates can include
   revisions to earlier days and are not necessarily that day's earned revenue.
   Label charts accordingly, show gaps, and disclose excluded monthly DR.
4. Handle 404 (no published estimate/selection), 503 (unavailable store), stale
   results, failed refreshes and partial coverage across primes. Keep usable
   static/cached data and disclose its source/as-of date; never turn missing
   data into $0 or allow one missing prime to blank the whole page.
5. Cache appropriately for a daily feed; use the API's ETag/Cache-Control where
   compatible with the dashboard's architecture. Do not add hourly backend
   computation, provider calls or an unbounded browser polling loop.
6. Keep provenance available in an unobtrusive detail view (revision, cutoff,
   computation time and version identifiers). Corrections under the same cutoff
   must update the displayed estimate when the published revision changes.

Validation:
- Exact decimal display and correct existing metric mapping.
- Current/stale/missing estimates; one-prime failure with others available.
- Month rollover, canonical monthly precedence, and history gaps.
- A revised result for the same cutoff; failed refresh retaining prior data.
- Backend down and 404 fallbacks without breaking the static dashboard/build.
- Verify against actual API responses when available; first daily publication
  may still be pending. Use fixtures for absent-data scenarios, never fabricate
  live results or claim every prime's live calculation has been verified.

Reference-rate provenance is in `data.input_provenance.reference_rates` for
subsidized primes. It includes source URLs, exact APR observations, revision
indicators, calendar version, snapshot ID, first observation time, coverage flag,
and `carry_forward_dates` mapping calendar dates to observation effective dates.
The daily worker waits for required official rates. On weekends/holidays, Grove
and Spark may remain stale until Friday's or a preceding business day's rate is
published on the next publication day; show the retained cutoff and attempt
state honestly. The schedule is 20:17 UTC after the Fed's same-day revision window.
