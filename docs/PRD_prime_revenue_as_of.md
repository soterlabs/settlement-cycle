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

Input reuse is an acceptance requirement for the eventual daily pipeline.
Step 1 establishes the accounting API; steps 2–5 are subsequent changes, not
claims that cache coverage or daily operating costs have already been verified.
Scheduling, result persistence and API publication follow those requirements.

## Step 1 scope and semantics

- API: `compute_monthly_pnl(prime, month, as_of=date(...))`; CLI:
  `settle run --prime obex --month 2026-08 --as-of 2026-08-15`.
- Reject dates outside the selected month, today, and future dates. An explicit
  cutoff represents a completed UTC day, not an intraday projection.
- Live HyperSync runs certify that each closing pin is the last block at or
  before the cutoff and is outside the configured reorganization margin. Archive
  lag fails the run instead of clamping to an earlier head. Explicit fixture
  pins with a custom resolver remain caller-certified test/replay inputs.
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
