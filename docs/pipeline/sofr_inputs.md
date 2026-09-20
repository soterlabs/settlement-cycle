# Daily reference-rate publication gate

Grove and Spark daily publication uses the New York Fed's official
[SOFR search API](https://markets.newyorkfed.org/api/rates/secured/sofr/search.json).
Values are selected by `effectiveDate`, not their publication date. `percentRate`
is divided by 100 using Decimal and remains a simple annual rate. The existing
borrowing formula caps the subsidized rate at the base rate: SOFR at or above
base never increases the charge above base.

Before every calculation or result-cache reuse, the worker refreshes the small
month-to-date observation window (one HTTP request per subsidized prime per
attempt). This detects corrections without invalidating finalized chain inputs.
It requires every business-day observation used, including an opening seed when
the month starts on a nonbusiness day. Missing/malformed/duplicate observations,
HTTP failures and unknown calendar coverage prevent publication. YAML SOFR is
never a fallback in this publication path. Historical pre-SOFR T-Bill inputs
still come from the configuration but require complete business-day coverage.

The versioned calendar in `config/sofr_calendar.yaml` contains the verified
2026–2027 U.S. full closures from [SIFMA](https://www.sifma.org/resources/general/holiday-schedule).
Early closes normally require their own observation. The NY Fed's
[April 3, 2026 notice](https://www.newyorkfed.org/markets/opolicy/operating_policy_260312a)
overrides the original Good Friday calendar: April 3 has no repo observation or
publication. Its [July 3 notice](https://www.newyorkfed.org/markets/opolicy/operating_policy_260618a)
allows publication of July 2's observation on the July 3 trading closure;
`extra_publication_days` records that distinction. Outside this calendar the
worker fails closed. Extend it from the official schedule before 2028, and update
it for any additional NY Fed closure announcements. Missing API rows never
create new holidays automatically.

The daily schedule is **20:17 UTC**, after both the approximately 08:00 New York
publication and 14:30 same-day revision window year-round. See the
[NY Fed publication rules](https://www.newyorkfed.org/markets/reference-rates/additional-information-about-reference-rates).
The previous UTC day remains the target. Friday observations normally publish
on Monday; weekend/holiday attempts that require an unpublished observation
are reported as deferred before a calculation attempt is created, retain the
previous result, and catch up after publication. The completion monitor applies
the same publication calendar at the last due scheduled run. A weekend carries
Friday's observation only once that observation actually exists. No extrapolated
business-day rate is published as confirmed input.

Validated snapshots persist immutably in `revenue_reference_snapshots`. Their
content hashes become part of the result's `input_revision`; corrected rates
produce new results without overwriting old revisions. `input_provenance` exposes
the source, exact APR observations, revision indicators, calendar hash, snapshot
ID, first observation time and each carried-forward date. Configuration changes
are checked before reuse and after calculation. Earlier published cutoffs are
restated explicitly with the bounded backfill command; subsequent MTD estimates
use the refreshed complete window. Output backfills remain limited to 90 days;
an older opening seed is an input, not an extra output backfill.

The API reads these stored snapshots/provenance only. It never calls the Fed or
runs a calculation. Source failure retains a prior publication and is visible
in the attempt ledger/status endpoint. Direct monthly calculations keep their
existing configured-input behavior; the strict gate belongs to daily publication.
