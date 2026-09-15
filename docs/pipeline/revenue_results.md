# Versioned daily results (PRD step 5)

Apply `db/schema_revenue.sql` to the same Postgres used for finalized inputs.
`settle.revenue.store.publish` writes within the caller's transaction; only a
committed row is published. Raw-input caches remain independent and reusable.

Identity includes prime, completed UTC cutoff, opening and closing block maps,
code version, configuration hash and input revision. An identical retry returns
the same revision. Different values under an identical identity raise an error;
operators must correct inputs/configuration/code and produce a new revision.
Previous revisions remain queryable. A late backfill cannot displace a newer
cutoff as the latest result. Decimal amounts are lossless JSON strings.

`capture_versions` uses the deployment commit (or a clean local Git revision),
a hash of every checked-in config YAML, and `SETTLE_INPUT_REVISION`. Capture before
calculation and recheck before publication to prevent mixed-version results.
The scheduler added in step 6 owns that lifecycle and the transaction boundary.

Published rows are always provisional daily estimates, including month-end
cutoffs. Monthly DR workbook enrichment and canonical monthly settlement are
separate; `excluded_inputs` explicitly lists monthly distribution rewards.
Only the last 90 completed UTC days can be newly published through this path;
older monthly reports remain the dashboard fallback. Existing stored revisions
remain readable after aging beyond that window.
