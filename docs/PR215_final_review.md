# PR #215 final review — 2026-10-29

Scope: monthly borrowing costs and yields on Sky-funded allocation principal.
Ultimate funding-origin research remains deferred as described in
[the scope note](PR215_scope.md) and [follow-up PRD](PRD_allocation_funding_next_steps.md).

## Rebase

Rebased onto main `800bad1`. Resolved conflicts by retaining main's venue views,
Basin idle-USDS deduction, workbook output-directory support and financial
reporting integration CI, alongside the explicit opt-in diagnostic analytics.
Normal revenue runs do not enable the full funding replay.

## First review and fixes

Reviewed the capital/cost path, daily deductions, rebase integration and preserved
settlement controls. Found that the readiness report authenticated the deduction
file's control hash but did not reconcile its actual daily deduction values.

- Reconcile extracted idle deductions against published daily totals before
  pricing allocations, reusing the existing control validator.
- Require exactly one rate row per period day.
- Reject negative/nonfinite daily principal, rates and deductions.
- Both saved August Spark/Grove idle files pass this validation. No numerical
  artifact or published settlement was regenerated.

## Second review and fixes

Reviewed the updated calculation boundary and monthly publication/API contract.
Found that mutually matching input artifacts could still publish numeric costs
for an unresolved funding status.

- Require costs for known-cost statuses and null costs for unresolved statuses.
- Require zero costs for excluded and zero-principal allocations.
- Preserve unknown Savings costs/net yields as null.
- The real publication dry run succeeds for 45 Grove and 90 Spark rows.

## Full-suite integration follow-up

The full unit run exposed strict historical snapshot decoding rejecting the new
optional allocation field. Add an explicit migration for absent
`allocation_financing` and/or `venue_views`, retaining strict validation of every
financial field and original historical payload hashes.

Four receipt-income tests also retained Robinhood ALM activation metadata after
restricting the fixture to another chain. Restrict that metadata consistently;
do not relax the production configuration invariant. All five failing cases now
pass, alongside regression tests for the migration and both review findings.

The final full unit and PostgreSQL CI runs are the merge gates. Deployment and
monthly artifact import are separate from merging; no production import or
historical revenue recalculation is performed by this review.
