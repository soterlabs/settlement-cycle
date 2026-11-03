# Conditional Spark allocation estimates in the monthly API

This publication is limited to Spark, August 2026. The immutable baseline is
enriched with separately labeled estimates for 56 allocations. It does not
replace canonical borrowing costs, revenue, exposure, yields or statuses, and
never changes daily revenues or settlement reports. Prior revisions remain
queryable. Schema 1.1 adds fields to the existing monthly endpoint.

Each supported row gains `conditional_funding_estimate` with
`status: conditional_estimate`, `funding_validated: false`, average Sky/Savings
principal, estimated Sky borrowing cost, net PnL, monthly net return and net APY.
Net means retained venue revenue minus estimated Sky cost; it excludes Savings
VSR expenses and prime-level financing adjustments. Null yields stay null.
S66 has an estimated cost of $2,545.24 but no net PnL or yield because its revenue
is unavailable. Existing unresolved canonical fields stay null.

Top-level `conditional_funding` reports unresolved reconciliation, the
$7,607,924.34 Savings principal outside captured accounts and the $231,391.92
unclassified Sky financing cost. These are different dimensions, not amounts
to add together. Its $5,530,034.10 allocation-cost total covers the 56 estimates;
do not combine some canonical costs with some estimates in the same total.

Publication requires the reviewed file hashes in
`reconciliation/spark_conditional_publication_2026_08.json`. The publisher checks
all candidate allocations, complete daily coverage, nonnegative principal,
aggregate costs and yields against unchanged revenue/exposure controls. The
Anchorage denominator remains audited outstanding loan principal.

## Backfill

Save the complete current API document, including `revision_id`, as BASE.json.
Then prepare a reviewable output without network calls or capital replay:

```sh
PYTHONPATH=src .venv/bin/python scripts/publish_conditional_allocations.py \
  --base BASE.json \
  --manifest reconciliation/spark_conditional_publication_2026_08.json \
  --output REVIEWED.json
```

After review/merge, rerun with `--publish` and the production `DATABASE_URL`.
The short transaction locks only the monthly-allocation table, verifies the
stored baseline and checks for a newer revision before inserting. Retrying the
same reviewed base and evidence is idempotent. It does not overwrite a newer
independent revision. No migration is required: the store already supports
immutable JSON documents and the API returns those documents unchanged.

Verify the latest and exact-revision API responses. Removing the new optional
fields must reproduce every original allocation row. Old revision URLs must
still return the original document. Dashboard presentation of these additional
estimate fields is separate; existing clients retain their canonical values.

The `conditional_funding.scope` is `whole_prime_month`, including on a
venue-filtered response. Its `financing_bridge` exposes other captured-account
financing, unclassified financing, Vat-rate financing and deduction differences.
The allocation-cost total plus these components equals global cost excluding
MSC within rounding tolerance. That arithmetic does not classify the unresolved
component or validate funding provenance.
