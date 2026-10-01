# September 2026 close validation

Status: in progress. Spark and distribution rewards are still running; this is
not a complete settlement package yet.

The September 30 API calculation is compared with a separate full-month
`compute_monthly_pnl(prime, Month(2026, 9))` execution, with `as_of` omitted.
The full monthly run does not load the API result as its calculation result.
Both executions use finalized source inputs, the same configuration and
reference-rate observations, and calculation commit
`c32f25a7ef410eaaf9212b8f546f652c715dc7ac` (merged PR #222).

The comparison checks every result field. Each JSON records the API revision,
input-version agreement, differences and complete-result hashes. Grove, Keel,
Skybase, Osero and Obex currently have identical hashes. Distribution rewards
are added from the separate September DR workbook when assembling settlement
reports; they are not part of this supply-revenue comparison.

## Reference rate

September 30 SOFR was not yet published at calculation time. Spark and Grove
use the previously authorized September 29 carry (3.88% APR). The API retains
`coverage_complete: false` and the operator-estimate provenance. Any reports
using this input must be marked preliminary until refreshed against the
published observation. The other four primes do not use this reference series.

## Grove Basin deduction

PR #222 applies only from September 1, 2026 and only to ALLOCATOR_GROVE_A.
The September 1–29 API transformation changes borrowing-cost fields and their
resulting PnL; supply-side revenue is unchanged. The September 30 calculation
includes the deduction directly.

For the full month, Grove borrowing interest falls from
3,873,580.395582812200319233744 to 3,754,292.984290454805445993200 USDS:
a reduction of **119,287.411292357394873240544 USDS**. Its 2,507,467.934719144391934655300
USDS SDE claim is separate from borrowing interest.

## Non-MSC and Gelato

September non-MSC net revenue is **708,599.21131559140975324007 USDS**.
The legacy DSR expense includes closing unpaid interest less opening unpaid
interest; it does not drop the tail after the last September `Pot.drip`.

Gelato returned **42,469.146527 DAI** to MCD_BLOW2 on September 7. The refund
was already moved to the surplus buffer on September 15. Recognition is at
receipt, and the subsequent cash recognition is offset, so September contains
the refund exactly once. The September non-MSC report links both transactions.

No pre-September settlement reports are regenerated and no on-chain
transaction is submitted. TMF parameters are calculated proposals, not a
claim that an October executive spell has executed.

## Operational recovery

HyperSync requests are paced across the close jobs. Distribution-reward page
caches and chunk checkpoints allow retries to preserve completed work.
Spark event timestamps were prefetched from fresh complete HyperSync log
queries, retaining only blocks outside the observed finality margin.
Historical `balanceOf` and `scaledBalanceOf` RPC reads were also batched and
stored under the existing finalized input keys, with no overwrites. An
alternate archive endpoint was cross-checked against 40 exact historical
reads before use. Neither operation approximates balances or changes yield
formulas; failed responses are never cached as zero.

The first DR chunk began before the extraction-cache improvements. It was
allowed to finish to preserve its work; later chunks use the updated extractor.
The reward and attribution formulas are unchanged. The conversion cutoff
change removes only later-day rows that the existing calculation already
filtered out. The output workbook is assembled with the pinned DR revision.

Non-MSC was calculated at commit `4506950`; later report changes clarify the
savings narrative and omit a holder split unavailable from the source.
`non-msc-validation.json` records the final totals and refund events, while
`dsr-accrual-check.json` records the independent boundary-state calculation.
The two DSR results differ only in the final Decimal-context digit (far below
one cent).
