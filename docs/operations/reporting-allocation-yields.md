# Reporting allocation yields

The yield page reads `GET /v3/reporting/allocation-yields?prime=spark&month=2026-09`.
Optional `venue` and immutable `revision` filters retain the same lossless decimal,
ETag, missing-data, and provenance contract as the monthly allocation publications.

This is the reporting read model for reviewed borrowing costs and gross/net
allocation yields. It reads published `allocation_monthly_results`; it does not
compute settlement amounts or perform chain reads during an HTTP request.
September Spark publications from PR283 include earned RLUSD/PYUSD incentives,
exclude those issuer cash receipts from yield, and retain validated allocation
borrowing costs. `revenue_basis` describes the selected publication; older
publications keep their original basis and must not be relabelled as accrual.

`/v3/reporting/allocations` is a different projection: native supply income only,
without external rewards or borrowing costs. It is not a complete yield source.
The legacy `/v1/revenue/{prime}/monthly/{month}/allocations` remains a deprecated,
read-only compatibility route for the same immutable reporting publication.
No settlement revenue, true-up, or report is changed by this endpoint migration.

Both routes return the same revision and values. The dashboard must not silently
fall back to native-only income or settlement revenue if the reporting yield
endpoint is unavailable. Missing months remain unavailable; this migration does
not backfill or recalculate data.
