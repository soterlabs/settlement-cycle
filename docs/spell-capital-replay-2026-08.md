# Executed-spell capital replay: August 2026

Completed 2026-09-23 for PR #215. **Neither Spark nor Grove reconciles.**
The historical corrections remove specific false matches/missing links;
most allocation funding histories remain unresolved. The numbers below are
**partial traced subtotals**, not estimates of the full allocation costs.
The large differences must not be presented as measured economic losses.

| Prime | Traced allocation subtotal | Unchanged global cost | MSC cost excluded | Adjusted target | Subtotal minus target |
|---|---:|---:|---:|---:|---:|
| Spark | 10,063.494722 | 6,108,910.339051 | 397,481.843094 | 5,711,428.495957 | -5,701,365.001236 |
| Grove | 6,304.101895 | 3,720,604.844326 | 259,878.117027 | 3,460,726.727300 | -3,454,422.625405 |

Spark's subtotal increases from **$9,540.906757** to **$10,063.494722**;
Grove's increases from **$6,222.614398** to **$6,304.101895**. Both published
global charges are unchanged. The MSC exclusion includes outstanding debt
from prior months and is applied only to the diagnostic comparison.

| Prime | Unmatched receipts, before → after | Unmatched outflows, before → after | Unresolved allocations |
|---|---:|---:|---:|
| Spark | 259,849 → 259,849 | 52,184 → 52,182 | 54 (unchanged) |
| Grove | 298 → 296 | 530 → 529 | 24 (unchanged) |

## What the code now accounts for

[`executed_spell_capital.py`](../src/settle/compute/executed_spell_capital.py)
links the exact executed transactions, with inline immutable source links:

- **July 28, 2025 portfolio purchase:** Grove's JTRSY funding cost is the
  spell's fixed **$404,016,484**, not the higher quoted NAV. Spark's full
  exit is linked to the same actual sale price. BUIDL is purchased at par.
- **July 20, 2026 syrupUSDC exchange:** Spark pays **$100,928,938.340794**
  first; Grove delivers 144 seconds later. Spark's explicit purchase loan
  passes through a pending account, isolated from unrelated reserve sweeps.
  Grove's existing borrowed basis moves into sale proceeds on delivery.
  An incomplete pinned history cannot borrow a future delivery.

In Spark, the previously unknown syrupUSDC receipt disappears. Isolating the
purchase draw exposes unrelated receipts in the payment spell as unresolved
instead of incorrectly funding them with the purchase loan. Consequently the
unmatched-receipt count is unchanged; this is not evidence that the correction
had no effect. Both purchase-related unmatched outflows disappear. Grove
loses the opening JTRSY unknown-funding receipt, the unmatched sale payment,
and the unmatched syrupUSDC delivery outflow.

These exact links do not repair all subsequent capital routes. Grove still
has delayed BUIDL payments, cross-chain investment movements, E21 off-chain
principal and E36 custody gaps. The BUIDL amount/date/fee candidate dataset
remains investigative and is not activated by this change. Earlier unresolved
receipts can propagate uncertainty through later reallocations in both primes.

## Validation and scope

- Both August financing replays completed from inception through the published
  end-of-month pins. Spark replays 451,903 adjusted batches from 451,902 raw
  batches; Grove replays 2,259. Spark's run took approximately 56 minutes.
- Both real snapshots pass execution-shape, idempotence and unchanged total
  draw checks. Raw normalized histories remain immutable.
- Every daily `frob + grab`, scaled by its ilk's Vat rate, matches the published
  debt control within one cent. All 11 nonzero historical MSC additions for
  each prime match exact Sky spell amounts and execution transitions; see
  [the MSC analysis](allocation-msc-reconciliation-2026-08.md).
- Spark's comparison covers ALLOCATOR-SPARK-A. Grove's comparison combines
  ALLOCATOR-BLOOM-A and ALLOCATOR-GROVE-A. It does not establish separate
  per-ilk allocation-cost attribution for Grove.
- **1,243 tests passed, 1 skipped** across the unit suite and monthly integration
  tests. Changed code/tests pass Ruff. GitHub test jobs did not start because
  account billing/spending limits blocked execution.
- Published settlement controls retain their hashes. Global debt, borrowing
  charges and settlement amounts are unchanged. The PR remains draft.

The machine-readable summary, source hashes and unresolved venue IDs are in
[`spell_capital_replay_2026_08.json`](../reconciliation/spell_capital_replay_2026_08.json).
Full local replay outputs are `/tmp/allocation-{spark,grove}-spell-fix/financing.json`.
The compute replay uses commit `1667c48`; the subsequent duplicate-input guard
adds rejection coverage without changing these valid histories' calculations.

Reproduce financing from a valid normalized snapshot and configured sources:

```sh
.venv/bin/python scripts/validate_allocation_financing.py \
  --provenance settlements/spark/2026-08/provenance.json \
  --history-dir /tmp/allocation-spark-spell-fix
```

Use the corresponding Grove paths for Grove. The script itself reports the
raw global comparison; the separate MSC-adjusted diagnostic is documented in
the linked MSC analysis. Do not regenerate published settlement artifacts.
