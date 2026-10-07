# Monthly settlement from a daily revenue revision

`settle monthly-from-revenue` explicitly finalizes one complete month-end MTD
result from the daily API's Postgres store. It does not add daily MTD results
together, run a revenue backfill, publish API revisions, or change the default
`settle run` workflow.

```sh
settle monthly-from-revenue \
  --prime grove --month 2026-09 \
  --revision <September-30-revision-id> \
  --output-dir /tmp/grove-september-review
```

Run after the month has fully closed in UTC and the daily worker has published
its final cutoff, including the required official reference-rate observations.
`DATABASE_URL` must point to that worker's store. The output directory must be
new or empty. Existing settlement artifacts are never intentionally overwritten.
The Excel renderer now reads and writes this directory too.
The command reports success only when provenance, summary and Excel files all
exist. A renderer failure returns a nonzero exit status and identifies any
partial output. After fixing the failure, retry with a new empty directory.
The normal package installation includes the required `openpyxl` dependency.

The command reads Postgres in a read-only transaction. It requires an explicit
revision rather than silently choosing whichever revision is newest during
settlement. Source code, all YAML configuration, and manual input revision must
match the running daily calculation. After a methodology/configuration change,
first produce a new month-end daily result with the matching deployed code.
There is no override for stale results and no automatic fallback to a live run.

## What is reused and what is calculated

* Reuse supply-side allocation revenue, positions, principal flows, SDE series,
  agent-rate revenue, Chronicle Points and pinned on-chain inputs from the final
  MTD snapshot. No RPC or indexer queries are necessary for this command.
* **Recalculate borrowing interest** with the existing `compute_sky_revenue_daily`
  function, using the saved daily ilk debt, idle/SDE deductions, SSR, subsidy
  configuration and original reference-rate observations. This is a validation
  calculation; retain the original exact saved settlement amounts on success.
* Check complete daily coverage, utilized debt, each day's net and gross charge,
  total gross interest, SDE income, spread reimbursements, supply revenue and
  the resulting Sky claim. Existing rate fields contain floats; validation allows
  at most USD 0.000001 of serialization noise. It does not round dollar amounts.
* Refresh monthly GAR using the existing consolidated-report dependency.
  The standard writer adds distribution rewards from the local DR workbook,
  using the same availability rules as an ordinary monthly run. Ensure the
  required workbook/consolidation artifacts are available before settlement.
* Record the exact daily revision, payload hash, code/config/input versions,
  reference-rate snapshot ID and recalculated interest in `provenance.json`.

The reference-rate snapshot is intentionally frozen. Calendar coverage and its
hash are validated; this command does not ask the official provider whether an
observation was subsequently corrected. To adopt corrected inputs, first
publish a new daily revision and explicitly select it for settlement.

Borrowing interest and total Sky revenue are not interchangeable when there are
SDE positions or spread reimbursements:

```
Sky claim = interest on utilized debt + SDE revenue − spread reimbursements
```

The existing subsidy caps, nominal APR convention and deduction rules are
unchanged. The interest is already represented in the saved Sky claim, so it
must not be subtracted a second time from monthly PnL.

## Validation and operational scope

Tests cover nonzero debt that changes during the month, crossing the subsidy
cap, changing SSR, fully idle debt, SDE income and reimbursements, calendar-based
reference-rate carry, missing/duplicate days, corrupt/stale/incomplete snapshots,
GAR refresh, real Postgres revision selection and isolated XLSX generation.
These are synthetic validation scenarios, not a live September reconciliation.
No September backfill or settlement publication is part of this change.
