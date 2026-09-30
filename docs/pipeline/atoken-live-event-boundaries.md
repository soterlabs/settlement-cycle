# Live aToken/spToken event boundaries

## Problem and change

The live Cat C/D calculation previously inferred event days from daily mint/burn totals and used end-of-day position balances. An intraday deposit's subsequent interest could therefore be booked as capital. Direct position-token transfers were not included in that event-day lookup.

For HyperSync-backed Aave aToken and SparkLend spToken venues, live source routing now supplies exact holder Transfer block numbers to the existing `Sources.atoken_event_blocks` calculation path. The lookup scans both incoming and outgoing transfers over `(opening_block, closing_block]`, includes ordinary transfers as well as mints/burns, deduplicates logs and returns sorted unique blocks. It uses the existing reorg-safe raw-log store; provider failures propagate rather than silently degrading to daily boundaries.

This fixes event-day timing and discovery. It does not introduce a new aToken valuation formula: the existing block-state arithmetic, integer rounding and zero-balance exit handling remain in use. In particular, block-minus-one and event-block states have different timestamps; they must not be described as having no elapsed time. This change is not a claim of transaction-exact accrual for every full-exit edge case.

Explicit event callbacks remain authoritative. Custom offline balance sources do not acquire a new live network dependency. The existing legacy daily fallback remains available for fixtures without captured event logs and non-HyperSync sources. No-event lookups retain the existing no-event behavior.

## Scope

Both daily estimates and monthly settlements call the shared computation, so future calculations use the corrected live boundaries.

| Prime | Configured affected venues |
|---|---|
| Spark | S1–S5 (SparkLend), S6–S9, S35, S41, S54 (Aave) |
| Grove | E1–E3 (Aave) |
| Osero | O1 (SparkLend) |

There are 16 configured venues, all using HyperSync. Dormant positions without movements need no capital-flow correction. Plain stablecoins, ERC-4626/savings vaults, Curve/Uniswap LP positions, and other RWA/EOA paths are outside this change. No cost-of-funds convention or debt calculation is changed; corrected revenue can change future settlement results.

## Forward-only rollout

Apply the fix to future calculation runs after merge. Do not backfill/replay prior settlement months, restate published settlement artifacts, or launch an explicit daily replay window as part of this change. A later user request authorized the read-only historical estimate documented separately; it does not authorize publication or restatement. No historical monetary overrides or activation-date special cases are introduced.

The ordinary daily worker continues its existing behavior: it calculates the latest eligible cutoff and fills unpublished gaps, rather than systematically republishing historical cutoffs when code changes. Its estimates are month-to-date, so a newly calculated cutoff uses corrected arithmetic for that period; older stored revisions remain unchanged. An explicitly requested historical rerun would use the corrected code and is outside this rollout.

## Validation

Synthetic future-period tests exercise first/additional deposits, partial/full withdrawals, multiple same-day events, ordinary incoming/outgoing transfers, self-transfer deduplication, opening/closing block limits, no-event periods, provider failures, and fixture precedence. A deposit of 1,000,000 with 100 of subsequent interest remains 1,000,000 capital plus 100 revenue. A subsequent user-authorized read-only historical event audit is documented in `atoken-event-review-and-estimate.md`; no historical settlement or API publication was changed.
