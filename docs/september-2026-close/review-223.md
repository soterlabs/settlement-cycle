# PR #223 review — October 1, 2026

Reviewed `60bad9d` against main. CI is green (unit and PostgreSQL integration).
No on-chain action or historical report regeneration was performed.

## Findings — addressed

1. **Fixed: P2 — The authorized SOFR close is not reproducible from the PR alone.**
   The September Spark/Grove provenance correctly records 3.88%, sourced from
   September 29 for September 30, with `coverage_complete=False`. However, its
   rate preparer lives only in `/tmp/run_september30_sofr_estimate.py`, and the
   report assembler is also outside the repository. The normal API-to-monthly
   loader (`src/settle/revenue/monthly.py:95`) rejects both saved snapshots as
   `invalid reference-rate snapshot`; the regular live runner does not inject
   the authorized snapshot. Commit a narrowly scoped, explicit override and a
   reproducible close command rather than weakening the general completeness
   guard. The operator has reconfirmed use of September 29 for this close.

2. **Fixed: P2 — Refund cash coverage is checked by day, not transaction.**
   `src/settle/compute/non_msc.py:261-268` sums all cash surplus returns on the
   settlement date. If the backend omits the Gelato return but includes an
   unrelated return of at least 42,469.146527 on that date, the guard passes
   and subtracts an offset for cash that was never booked. Reproduction:
   one unrelated 50,000 cash row plus the Gelato offset is accepted and yields
   7,530.853473 income. Bind the offset to the specific Blow/join transaction
   in the cash extraction, retaining enough identity to check it. The actual
   September Gelato cash transfer was separately verified, so this is not
   evidence of an error in the current September total.

## Confirmed

- Submodule is exactly `1e9ecb2`; finalized DR input matches the requested
  September venue amounts and Grove Farm emitted-code split.
- Four historical Skybase corrections total 124,694.330541 and appear once,
  separately from normal September revenue. Prior reports remain unchanged.
- 1997–1999 and 1020 resolve to Skybase; deliberately unpaid/unresolved codes
  stay excluded and disclosed.
- Redundant independent DR replay and its watcher are stopped. The upstream
  finalized output is authoritative; no downstream replay is required.
- SOFR September 29 is 3.88%. New York Fed scheduled publication is around
  08:00 America/New_York, or 12:00 UTC during daylight saving time. September
  30's observation is therefore expected October 1 around 12:00 UTC. The
  public five-observation feed does not provide exact publication timestamps.

Sources: https://www.newyorkfed.org/markets/reference-rates/sofr and
https://markets.newyorkfed.org/api/rates/secured/sofr/last/5.json.

## Fixes

Both findings are addressed. `reproduce.md` documents the committed September
close command and narrowly scoped opt-in for API-to-monthly reuse. The cash
source now preserves transaction/log identities and the offset guard matches
booked cash by transaction; unrelated same-day receipts cannot satisfy it.
Regression tests cover opt-in/default behavior, altered/missing rate inputs,
wrong scope, missing cash identity, and duplicate cash rows.

Validation: 1,281 unit tests passed (one optional Crypto dependency skip).
`review-fix-validation.json` records successful revalidation of Spark/Grove
interest and a full September non-MSC rerun: totals unchanged, Gelato cash
matched by transaction and log index.

## Final reporting finding — addressed

The close command previously attached the reference snapshot only after
rendering, so a rerun lost the visible SOFR disclosure. The writer now saves
that snapshot before either renderer runs. Markdown and the workbook Summary
display the approved 3.88% carry-forward; DR-only refresh retains it. The
consolidated Sky and TMF reports inherit the disclosure as well. Regenerated
September artifacts were checked against the existing financial values: no
amounts changed. Tests cover repeated writes, real workbook generation,
DR-only refresh, official-rate inputs, and downstream propagation.

Final disclosure validation: 1,285 unit tests passed (one optional Crypto skip).
Spark/Grove financial provenance and consolidated Sky/TMF financial values
were unchanged by report regeneration.

## Review after PR #218 rebase and isolated S1/E10 refresh

Reviewed the rebased close changes and new BUIDL ledger, the selected-venue
recalculation, borrowing-cost dependencies, DR/true-up separation, Gelato
receipt/cash offset, reference-rate guards and generated report differences.
No remaining blocking finding was identified in the supported settlement path.

Findings addressed in this update:

- Sub-$1M BUIDL redemption exits were being dropped by the incoming-yield
  filter. Restore the three verified 1,000-share September exits as capital;
  their combined $2,998.50 carrying value is not a revenue loss. The first
  draft narrative mentioned only one; the recorded calculation included all
  three. The exact bridge is now generated and regression-tested.
- Booking the September boundary cost in E10 and also adding it to `sky_adj`
  would double-count it. Keep historical `sky_adj` at -165,013.90; book the
  September cash variance once in E10 before its 100% SDE split.
- Adjustment-only `msc_preview` entries suppressed the old missing-preview
  warning without supplying published mint/send pins. Partial or absent pins
  now remain visibly unreconciled; this does not change amounts.
- The isolated runner must fail if workbook generation fails, rather than
  reporting a completed refresh with only Markdown/provenance.
- Original exact API/monthly comparisons are baseline evidence. The refreshed
  reports diverge from old Spark/Grove API revisions; README and PR wording
  now state that explicitly. The API updater is only a follow-up PRD.

The $331,666.079223 E10 reduction is entirely SDE. The separate marked-SDE
balance change adds $516.07959982 to Grove borrowing costs and reduces its
payment accordingly. Neither amount touches other venue native revenue.

Validation: 1,320 unit tests passed, one optional Crypto-dependent test skipped.
Focused offline replay tests also passed after adding the exact revenue bridge.
The two-venue replay is repeatable, performs no network/API writes, reproduces
the original E10 baseline, and preserves every unselected venue and unchanged
borrowing input. No new Ruff diagnostics; diff whitespace checks pass.
Postgres integration was not rerun without a configured disposable test DB.

Limits: the BUIDL matcher consumes whole requests, including batches. Partial
payments of a single request require allocation support; ambiguous matches
fail rather than guessing. The audited September data needs neither exception:
14 receipts match, with no outstanding or unmatched payments. Later request
ages and month boundaries are supported without a rolling lookback cutoff.
