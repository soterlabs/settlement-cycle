# Daily revenue read API (step 7)

The existing `settle-api` serves `/v1/revenue/{prime}/latest`,
`/v1/revenue/{prime}/at/{cutoff}?revision=...`, `/history?start=...&end=...&limit=90`,
and `/revisions/{cutoff}?limit=100`. The latter two suffixes are also under
`/v1/revenue/{prime}`. Requests only read committed database rows. Historical
windows are bounded to 90 days per request; older retained revisions stay readable.

Latest and cutoff responses expose the complete result plus its versions, pins,
computation time, provisional label, excluded inputs and latest attempt status.
Decimal values remain strings. Latest-result freshness compares the returned
cutoff to yesterday UTC. A requested historical cutoff is evaluated against that
requested date instead. An old successful result survives a failed refresh.
No published selection returns 404; unavailable storage returns 503 with no
connection details. These conditions must not become zero revenue.

`/v1/revenue/status` returns 503 when any prime is missing yesterday's publication
or its latest attempt failed/was abandoned. It uses `Cache-Control: no-store` and
is suitable for a daily-completion monitor. `/healthz` remains process/DB health;
it is intentionally separate from data freshness so delayed inputs do not cause
API restart loops. Result documents retain the existing ETag/300-second cache
contract. The daily scheduling cadence does not imply requests trigger refreshes.

See `msc_dashboard_daily_revenue_prompt.md` for the dashboard implementation
handoff, including static monthly fallbacks and correct MTD semantics.

`data.input_provenance.reference_rates`, when applicable, contains the validated
SOFR/historical reference snapshot: exact observations, source, revision markers,
calendar version and carried-forward dates. Daily publication requires complete
reference-rate coverage; freshness by cutoff does not substitute for that gate.
See [SOFR inputs](sofr_inputs.md) for publication delays and correction handling.
