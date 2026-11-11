# Signed Spark idle/bridge reconciliation

This replaces the additive historical bridge-credit estimate with a comparison
against the deductions actually used in the frozen reports. It changes no
production rate, live adjustment, saved settlement, API revision or payment.
PR #277 is neither modified nor merged into this branch.

## Headline results (USDS)

| Bridge-active earning dates | Credit to Spark (+) / additional cost (-) |
|---|---:|
| June 20–30 | +107,273.55 |
| July | -643,563.16 |
| August | +103.42 |
| September | +92.14 |
| **January–September component total** | **-536,094.04** |

January–May and June 1–19 are outside this component. June 20–21 are included
because PSM3 withdrawals left 101,905,703.611569 USDS idle in L2 ALMs, even
though bridge claims only start on June 22. These two days add 21,452.86 USDS
of credit at the frozen rate convention.

The old #277 item proposed `sky_adj: -255869.48`. The corrected replacement is
**`sky_adj: +536094.04`**, an **+791,963.52** change relative to #277. Replace the
old item once; never add the full new item on top of the old one.

The June correction includes wallet idle omitted from the frozen deduction;
July includes recovery of stale wallet idle after bridge completion. These are
a combined idle-plus-bridge comparison, not a claim that all changes are
previously charged bridge interest.

October remains **+75,063.88** of ordinary-month credit against the retained
pre-bridge-policy October inputs. That is separate from the historical ledger
and must not be added again to an October close that already uses the policy.
No new October adjustment to PR #275 is proposed here.

## Combined view with PR #277

This is an arithmetic preview using the immutable #277 head `ec89f0b`, without
rebasing, merging or changing that PR. Positive Sky adjustments increase debt
minting; negative ones give debt relief. Other #277 components are preserved.

| Prime | Supply-side income adjustment | Sky/debt adjustment | Send-only refund |
|---|---:|---:|---:|
| Spark | +249,141.52 | **+536,094.04** | 0.00 |
| Grove | +11,554.74 | -110,419.56 | 0.00 |
| Osero | 0.00 | 0.00 | +5,514.00 |
| Obex | -273,508.90 | 0.00 | 0.00 |

These are calculated historical adjustment proposals, not executed payments.
Final MSC uses its normal supply-side floor and whole-USDS rounding. Existing
October adjustments must be netted once. The prospective Morpho PRD is excluded.

## Why the earlier credit was wrong

Both the original July MSC #11 workbook and the frozen `536f149` version deduct
436,845,703.61 as ALM idle on July 1–30. Archive balances on July 6–12 are 90M
in wallets plus 346,845,703.611569069692530370 in large bridge claims. The old
idle deduction therefore already includes those large claims. The earlier
audit checked only for a separate bridge column, then subtracted them again.

After the large deliveries on July 13, the raw wallet balance remains about
90M, but the frozen deduction stays at 436.846M until July 30. That stale
deduction creates an undercharge. The 30,000 of small June claims remains
pending through September; it is retained separately and counted once.

For each scoped idle/bridge day the new calculation uses:

`deduction change = verified wallet USDS + eligible transit - frozen wallet idle - frozen transit`

Apply that signed change to the frozen utilized amount, reproduce the original
charge/rate convention, and calculate original charge minus corrected charge.
Rates, subsidy caps, the zero-charge floor and all other daily deductions are
preserved. Unresolved rate conventions or incomplete evidence cannot be credits.

## Evidence, prior netting and limits

- `wallet-inputs.json`: 670 archive balances across five chains and 134 daily
  closes, June 20–October 31. Each has adjacent linked block headers certifying
  the exact UTC boundary; chain IDs, owners, native token and units are checked.
- `sources.json`: hashes for raw evidence, original report workbooks, gateway
  fixture, original October inputs, prime configuration and #277 checkpoint.
  The replay requires the complete source set and hashes the actual consumed
  files even when checking a copied evidence package.
- `results.json`: exact signed daily/monthly comparisons and combined totals.
- `pr277-checkpoint.json`: immutable combined-ledger input from #277 `ec89f0b`.
- `original-july-workbook.xlsx` and `original-june-workbook.xlsx`: original
  published MSC #11 and prior-reconciliation baseline witnesses. All daily
  idle deductions must match the frozen baseline; rate conventions are not
  changed by this check.
- Original gateway capture/coverage and October input identities remain in the
  parent directory. The old `audit.json` is retained as a superseded estimate;
  its additive credit is not the new result.

The earlier reconciliation II covers January–June at `f5fa37e`; reconciliation
III's Spark item is reserve-factor income. The checked records identify no
additional payment for this idle/bridge correction. This proposal preserves
the frozen rate conventions and every other #277 prior-netting item. It does
not certify payments outside those recorded sources or assert on-chain execution.

This is a read-only component replay, not a new complete monthly ALM transfer
review. It does not certify arbitrary bridge families or unconfigured wallets.

## Reproduce

From repository root, with the package installed:

```sh
PYTHONPATH=src python -m scripts.reconcile_usds_bridge_baseline_2026 --output /tmp/bridge-baseline-replay.json
```

The replay blocks network access, validates all source hashes, replays gateway
claims at the October cutoff, verifies every wallet/date boundary and fits the
original daily charge conventions. The frozen October baseline is read from
retained inputs even when a newly generated October workbook is present.

Optional fresh archive recapture, into a separate checkpoint:

```sh
PYTHONPATH=src python -m scripts.capture_bridge_idle_wallets \
  --pin-source reconciliation/usds-bridge-2026/baseline-correction/wallet-inputs.json \
  --env-file /path/to/local.env --output /tmp/verified-bridge-wallets.json
```

The current bridge audit CLI accepts `--wallet-inputs`; without verified wallet
evidence it reports unmeasured days, never an additive refund. When #277 is
later updated, its builder must adopt the complete replacement comparison and
replace its old bridge item before regenerating the forum/payment proposal.
The public comparator rejects missing idle or wallet inputs; the private
rate-fitting diagnostic alone does not establish refund eligibility.

Both requested review/fix rounds and their validation are recorded in
[review.md](review.md). The final focused suite passes 93 tests; an independent
offline replay produces byte-identical results.
