# Review and fix record

## Round 1 — initial implementation `3c35eba`

- **High: missing idle could still reach additive arithmetic.** The public
  comparator retained a rate-only fallback for rows with no idle field. A
  financial caller omitting that field could therefore revive the original
  refund error. Require frozen idle and verified wallet balances for every
  public comparison; isolate legacy arithmetic in a private diagnostic helper.
  Regression tests reject missing idle as well as missing wallet evidence.
- **High: October baseline could depend on an unrelated regenerated report.**
  The general live audit preferred any local October workbook over the retained
  original daily inputs. Use one frozen-baseline selector in both replay paths;
  October always uses the retained original data, and unavailable data is
  unmeasured. Tests reject any attempt to read an October workbook as baseline.
- **Evidence check:** retain original July MSC #11 (`616ce3a`) and the June
  prior-reconciliation (`f5fa37e`) workbooks. Verify every daily ALM-idle
  deduction is unchanged from the `536f149` baseline. Different rate conventions
  remain frozen independently; no rate restatement is introduced.

Validation after fixes: 48 focused tests passed. Offline replay retains all
financial headline values. Neither #277 nor any settlement/configuration was
modified.

## Round 2 — fixed implementation `dd3168d`

- **High: copied evidence packages could hash a different wallet file than
  they consumed.** The optional replay folder changed the read location while
  manifest checks still resolved against the repository copy. Resolve package
  inputs consistently for both hashing and consumption. Tests mutate a copied
  wallet file and require rejection before calculating any credit/debit.
- **Medium: a missing hash or published-baseline check could weaken evidence
  validation.** Require the full closed source set and both original June/July
  idle comparisons. Tests drop each required check and require failure.
- **Medium: capture checkpoints lacked strict witness/scope validation.**
  Require proper block-hash encoding, ASCII uint256 quantities and compatible
  checkpoint date scope. Revalidate cached headers before marking a recapture
  complete; a failed validation must leave an incomplete checkpoint. Regression
  tests cover malformed quantities, header linkage and checkpoint scope.

Validation after fixes: **93 tests passed**, including the bridge audit/replay,
actual gateway routes, capture failures, rate/subsidy/floor behavior and the
unchanged production borrowing formula. A fresh network-blocked replay is
byte-for-byte identical to committed `results.json`. New scripts/tests pass
Ruff; the diff passes whitespace checks. Financial headline values and all
non-Spark #277 totals remain unchanged after both review rounds.

Both review rounds were performed in this task. PR #277 remains at `ec89f0b`;
neither PR is merged. Repository CI is reported separately on the PR.
