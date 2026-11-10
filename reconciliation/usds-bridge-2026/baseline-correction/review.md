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

## Round 2

Pending review of the fixed branch.
