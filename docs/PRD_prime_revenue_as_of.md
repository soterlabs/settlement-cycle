# Prime revenue as of a date

## Objective

Produce a reproducible month-to-date revenue estimate for each prime through a
completed UTC day. Reuse finalized inputs across daily runs, while initially
recomputing month-to-date arithmetic. A change between two estimates can include
revisions to earlier days; it is not automatically that day's earned revenue.

## Delivery steps

1. **Add `as_of` with correct partial-month accounting.** Accept an explicit date
   in the settlement month; calculate from the first day through its UTC close.
   Keep the previous month-end opening balance. Apply rates, flows, valuation,
   SDE allocation and accrual only through the cutoff. Pin live reads to complete,
   finalized chain boundaries. Label partial results provisional, keep them
   separate from canonical monthly reports, and disclose unsupported monthly
   inputs. The last day of a completed month must reproduce the existing full
   calculation. Omitting `as_of` retains the existing monthly interface.
2. **Audit and complete input-cache coverage in Postgres.** Reuse the existing
   finalized HyperSync event ranges and block-pinned RPC cache. Inventory every
   extraction path, its cache key, coverage, finality and correction behavior.
   Persist inputs across worker restarts. Fetch missing ranges and snapshots,
   not the same historical inputs every morning. Do not cache failed reads as
   zeros or incomplete ranges as complete.
3. **Verify same-date reruns.** With a warm persistent cache, repeating the same
   cutoff must make no unnecessary historical provider requests. Separate
   legitimate freshness/finality checks from historical extraction. Test both
   process restarts and recovery from interrupted runs.
4. **Measure one-day advancement.** Record HyperSync and RPC calls, downloaded
   data, cache hits, extraction time and calculation time when advancing the
   cutoff one day. Use those measurements to size provider plans and decide
   whether incremental arithmetic is necessary. Require reused historical
   coverage; establish numerical budgets from measurements, not an assumed
   thirtyfold increase.
5. **Keep input and result caching separate.** Finalized raw inputs remain
   reusable across calculation versions. A cached result must identify its
   prime, cutoff, opening/closing block pins, code version, configuration
   version, and input revision. Corrections produce new result revisions;
   previously published estimates remain auditable. Persist daily results in
   the database rather than committing daily artifacts to Git.
6. **Schedule the daily revenue pipeline.** Run once per day at a configured
   UTC time, calculating each prime through the previous completed UTC day
   (`as_of`), with the settlement month derived from that cutoff. This is a
   daily pipeline, not an hourly or intraday refresh. Require finalized inputs
   and Postgres persistence; retry archive lag or transient failures without
   moving the cutoff or publishing incomplete results. Make retries idempotent,
   prevent overlapping runs for the same prime/cutoff/revision, support missed-day
   catch-up and explicit backfills, and record per-prime status, errors and
   completion times. Alert on failures or missed daily completion. Verify retry,
   overlap, restart, catch-up and month-boundary behavior before enabling cron.
7. **Publish daily revenue through a read API.** Serve successfully persisted
   results from step 5, with endpoints for each prime's latest available cutoff,
   a requested cutoff, and historical revisions. Return the cutoff, computation
   time, block pins, code/configuration/input revisions, provisional status and
   unsupported or excluded inputs alongside the revenue fields. Expose freshness
   so consumers can distinguish a current result from an older successful result
   retained after a failed daily run. Publish each result atomically; failed or
   partial writes must never become the latest result. API reads must not trigger
   calculations or upstream provider calls. Verify revision selection, missing
   dates, stale results and failed-run behavior. The API follows the daily
   pipeline's publication cadence; it does not introduce hourly computation.

Input reuse is an acceptance requirement for the eventual daily pipeline.
Steps 1 and 2 establish the accounting API and persistent input infrastructure.
Steps 3 and 4 verify reuse and operating costs; step 5 establishes auditable
result persistence before daily scheduling and API publication in steps 6 and 7.
Implementation and operational acceptance are tracked separately below;
implementation alone is not a claim that every live venue has been verified.

## Step 1 scope and semantics

- API: `compute_monthly_pnl(prime, month, as_of=date(...))`; CLI:
  `settle run --prime obex --month 2026-08 --as-of 2026-08-15`.
- Reject dates outside the selected month, today, and future dates. An explicit
  cutoff represents a completed UTC day, not an intraday projection.
- Live HyperSync runs certify that each closing pin is the last block at or
  before the cutoff and is outside the configured reorganization margin. Archive
  lag fails the run instead of clamping to an earlier head. Explicit fixture
  pins with a custom resolver remain caller-certified test/replay inputs.
  Automatic pins use a separate finalized-resolution cache: failed attempts
  are not cached, and cold resolution bypasses legacy timestamps so retries
  recover after a reorg. Cached pins still undergo fresh boundary validation.
  The run-local resolver also uses finalized caches for daily debt, PSM/SDE
  valuations, event dates and cross-chain redemption timestamps; downstream
  lookups must not switch back to legacy resolution after pins are certified.
- Existing closing-position rules (including capped SDE allocation) use the
  cutoff position provisionally. NAV and configuration corrections can revise
  results; month-to-date estimates are not additive final daily earnings.
- Partial reports contain provenance and a clearly labelled summary, without
  monthly XLSX generation or monthly distribution-reward enrichment. DR is
  explicitly excluded until an input supporting the same cutoff is available.
- Active GAR depends on consolidated monthly Sky Net Revenue. Partial-month
  calculations requiring GAR fail explicitly; they do not prorate a full-month
  artifact or represent an unavailable reward as a confirmed zero. Retired or
  not-yet-active GAR programs do not block a run.
- End-of-month explicit cutoffs use the same calculation and report path as
  ordinary completed-month runs. Existing names such as `value_eom` and
  `monthly_pnl` remain for compatibility; the result period defines their scope.

## Step 1 acceptance checks

- Validate dates before extraction; cover leap days and month boundaries.
- Compare explicit last-day and ordinary monthly results across every field.
- Verify opening/closing pins, day count, daily rate changes, capital flows,
  closing valuations and SDE behavior on partial periods with deterministic
  sources, including events after the cutoff that must not contribute.
- Reject lagging, incorrect and unfinalized live closing pins.
- Ensure previews cannot overwrite canonical artifacts or include monthly DR;
  reject active monthly-only GAR with a clear error.
- Existing offline tests and targeted regressions pass. Record separately any
  live comparisons actually run; do not claim historical parity from mocks.

## Step 2 implementation

The [input-cache audit](pipeline/input_cache_audit.md) inventories the configured
prime-revenue extraction paths, coverage/finality contracts, correction
revisions, worker settings and validation. Daily workers require Postgres via
`SETTLE_REQUIRE_POSTGRES=1`; finalized raw inputs and complete event intervals
persist across fresh worker processes. Step 3's all-prime provider-call
acceptance checks and step 4's operational measurements remain separate work.


## Steps 3–7 delivery

- Steps 3–4: [same-date reuse and advancement measurements](pipeline/revenue_verification.md),
  delivered in PRs #207 and #208. The six-prime Postgres matrix uses deterministic
  transport; live same-date reuse passed for Grove and OBEX. Remaining live
  measurements must not be inferred from that fixture matrix or extrapolated
  into a provider budget.
- Step 5: [immutable daily results](pipeline/revenue_results.md), PR #209.
- Step 6: [daily worker and operations](pipeline/revenue_operations.md), PR #210.
- Step 7: [read API](pipeline/revenue_api.md), PR #211, including the
  [official SOFR publication gate](pipeline/sofr_inputs.md) required to safely
  resume daily publication. The daily schedule is 20:17 UTC; unavailable official
  observations retain prior results and are caught up after publication.
- [Dashboard implementation handoff](pipeline/msc_dashboard_daily_revenue_prompt.md)
  and [three-round review record](pipeline/revenue_delivery_reviews.md).

Daily outputs/backfills are bounded to the last 90 completed UTC days; older
monthly data remains canonical. Reference-calendar maintenance and external
alert delivery configuration are documented operational responsibilities.


## Operational acceptance follow-up

[Live measurements and independent completion monitoring](pipeline/revenue_operational_acceptance.md)
are tracked separately from implementation. The verifier uses the publication
reference-rate gate; a GitHub Actions check at 03:00 UTC detects missing due
results after the 20:17 UTC job's deadline. Required-Postgres reads never import
local-only fixture/cache entries. Notification receipt requires separate proof.
