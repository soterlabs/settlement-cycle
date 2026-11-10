# Settlement Reconciliation - MSC #5-#13 (January-September 2026)

Draft prepared for the October 2026 earning cycle (MSC #14, paid in November).
October's ordinary settlement will be presented separately.

This is an incremental reconciliation of corrections merged after the code used
for the last MSC, [`536f149`](https://github.com/soterlabs/settlement-cycle/commit/536f1492089ed82fbf6eaabb956f7a194b887d47),
through [`bda6858`](https://github.com/soterlabs/settlement-cycle/commit/bda68582defce86daeb1c7d5d3f6d5da03f18df9).
It follows the [January-August reconciliation](https://forum.skyeco.com/t/settlement-reconciliation-msc-5-12-january-august-2026/28273).
The published reports and previously settled adjustments remain the baseline.

## Historical adjustment ledger

All figures are USDS. Positive supply-side adjustments add income; negative
ones recover overstated income. A negative Sky-share adjustment gives the prime
debt-mint relief. Send-only refunds create no additional debt.

| Prime | Supply-side adjustment | Sky-share adjustment | Send-only refund |
|---|---:|---:|---:|
| Spark | 1,338,239.62 | -255,869.48 | 0.00 |
| Grove | 8,249.94 | -110,419.56 | 0.00 |
| Osero | 0.00 | 0.00 | 5,514.00 |
| Obex | -273,508.90 | 0.00 | 0.00 |

Each independently booked component is rounded to cents, preserving the already
approved components. Monthly rows below show unrounded components at display
precision; their sum can differ by a cent from booked totals. The final MSC
applies its existing supply-side floor and whole-USDS rounding.

## Method and prior settlements

The [calculation package](https://github.com/soterlabs/settlement-cycle/pull/277)
contains the PR inventory, immutable input hashes, monthly component ledger,
transaction evidence, offline replay and proposed October adjustment values.
This is a component replay using existing revenue, ownership and borrowing-charge
mechanisms. Original dates, NAVs, subsidy caps and daily rate conventions are
preserved. Historical workbooks are not overwritten.

Earlier adjustments remain excluded: Spark's 2,392,354.07 reserve-factor credit
and Grove's 165,013.90 BUIDL/CoF credit were handled in the previous post. The
2,508.55 August in-flight credit within Grove's earlier total is explicitly
subtracted from the new redemption calculation. Previously credited BUIDL fees
are also subtracted before computing the small remaining correction.

## Spark

- **Native-USDS bridge CoF: 255,869.48 credit**, June-September. Eligible USDS
  remains exempt during authenticated bridge transit when it was exempt on the
  source chain. The replay removes the missing claim from utilized debt using
  the original daily charge convention. It creates debt-mint relief only.
- **September partner receipts: 1,373,208.00 of supply-side income.** Six native
  RLUSD payments reached Spark's ALM. Complete payer/recipient history shows
  no earlier payment in the historical earning window. These are disjoint from
  the spToken receipts included in the lending venue below.
- **September missing investment income: 6,074.16.** Sentora RLUSD S72 adds
  3,674.16; SparkLend RLUSD S70 adds 2,400.00, including 113.73 of reserve
  receipts. The existing event-day NAV and lending-yield conventions are retained.
- **Maple async capital/income: -41,042.54**, January-July. Retaining queued
  shares and using actual paid cash corrects S14/S15's earlier capital/income
  treatment. The replay first reproduces the published venue income. This is
  an income debit, with no change to whole-prime CoF or rate formulas.

Spark's net supply-side adjustment is **1,338,239.62**, separate from its
**255,869.48** Sky-share/debt credit. The RLUSD receipts and investments here
belong to Spark; Grove's idle RLUSD was already tracked in the baseline.

## Grove

- **Redemption CoF: 78,530.06 net credit.** Includes the JAAA settlement-day
  debit, JTRSY/BUIDL corrections and deduction of the earlier 2,508.55 credit.
  This historical amount is already configured for October and is counted once.
- **Issued-deposit CoF: 35,889.68 additional credit**, January/March/April.
  Issued but uncollected JTRSY shares retain their applicable SDE treatment.
  The calculation is marginal to corrected redemptions, so shared dates and
  subsidy-tranche crossings do not receive a second deduction.
- **Small BUIDL capital/fees: 4,000.18 debit.** Restore 4,001.00 of share
  principal previously omitted by the small-transfer filter, less 0.82 of
  additional cash-cost credit after subtracting previously credited fees.
  September's already-recognized boundary fee and transition markdown are excluded.
- **Base Steakhouse vault E43: 7,219.49 of September income.** Complete owner
  share history starts in September; there is no earlier 2026 holding to true up.
- **Diamond PAU Uniswap E44: 1,030.45 of income**, comprising August 27.90 and
  September 1,002.55. The position is additional to the legacy ALM's position.
  The pool has no USDS leg, so adding its income creates no new CoF exemption.

The booked total is **110,419.56 of Sky-share/debt relief** and **8,249.94 of
additional supply-side income**. Warehouse principal and Basin presentation
changes redistribute venue CoF/reporting but add no separate headline credit.

## Osero

Osero's full borrowing cost was already financed through its allocator debt.
The historical deficit also reduced its demand-side transfer, charging the gap
a second time. Refund only that second deduction through `send_credit`.

| Earning month | Executed transfer | Correct transfer | Refund |
|---|---:|---:|---:|
| 2026-07 | 12,043.00 | 12,149.00 | 106.00 |
| 2026-08 | 30,156.00 | 31,604.00 | 1,448.00 |
| 2026-09 | 27,661.00 | 31,621.00 | 3,960.00 |
| **Total** | | | **5,514.00** |

These are differences between actual rounded transfers, not a fresh rounding
of the three deficits. The refund is already configured in October and must
not be added again. It increases the transfer only, with **zero debt-mint increase**.

## Obex

The shared async follow-up identifies **273,508.90 of overstated July income**.
Without queue routing, withdrawals had been replaced by a synthetic month-end
capital outflow. Actual dated queue movements and paid cash reduce that income.
Apply **-273,508.90 as a supply-side adjustment**. Whole-prime CoF is unchanged;
changes in venue attribution are reporting effects, not another credit.

## Monthly historical components

| Month | Spark SV | Spark Sky share | Grove SV | Grove Sky share | Osero refund | Obex SV |
|---|---:|---:|---:|---:|---:|---:|
| 2026-01 | -9,948.93 | 0.00 | 0.00 | -19,731.34 | 0.00 | 0.00 |
| 2026-02 | -39.47 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| 2026-03 | -7,744.26 | 0.00 | 0.00 | 19,783.82 | 0.00 | 0.00 |
| 2026-04 | -11,195.95 | 0.00 | 0.00 | -7,671.90 | 0.00 | 0.00 |
| 2026-05 | -1,726.69 | 0.00 | 0.00 | -65,481.22 | 0.00 | 0.00 |
| 2026-06 | -29.08 | -28.42 | 0.00 | 0.00 | 0.00 | 0.00 |
| 2026-07 | -10,358.16 | -255,655.55 | 0.00 | 0.00 | 106.00 | -273,508.90 |
| 2026-08 | 0.00 | -93.37 | 27.90 | -4,017.11 | 1,448.00 | 0.00 |
| 2026-09 | 1,379,282.16 | -92.14 | 8,222.04 | -33,301.81 | 3,960.00 | 0.00 |
| **Booked totals** | 1,338,239.62 | -255,869.48 | 8,249.94 | -110,419.56 | 5,514.00 | -273,508.90 |

## Settlement effects and booking once

The full historical ledger has the following linear effects relative to the
ordinary October settlement. Mint effects assume the combined supply-side share
remains above the existing mint floor; the October calculation must apply the
normal floor and final rounding to its actual ordinary figures.

| Prime | Debt mint change | Transfer change | Sky settlement net change |
|---|---:|---:|---:|
| Spark | 1,082,370.14 | 1,338,239.62 | -255,869.48 |
| Grove | -102,169.62 | 8,249.94 | -110,419.56 |
| Osero | 0.00 | 5,514.00 | -5,514.00 |
| Obex | -273,508.90 | -273,508.90 | 0.00 |
| **Total** | 706,691.62 | 1,078,494.66 | -371,803.04 |

Sky settlement net means debt minted less transfers to agents. A supply-side
income correction changes mint and send equally in the positive-share case.
CoF relief uses `sky_adj`; it is not credited again through `sv_adj`. Osero's
send-only refund reduces Sky's settlement net without issuing a second loan.

Because Grove's 78,530.06 credit and Osero's 5,514.00 refund are already configured,
the **remaining additions to the existing October entries** are:

| Prime | Additional SV adjustment | Additional Sky-share adjustment | Additional send refund |
|---|---:|---:|---:|
| Spark | 1,338,239.62 | -255,869.48 | 0.00 |
| Grove | 8,249.94 | -31,889.50 | 0.00 |
| Osero | 0.00 | 0.00 | 0.00 |
| Obex | -273,508.90 | 0.00 | 0.00 |

The calculation package includes combined October values that preserve ordinary
October receipts. It does not claim the configured historical credits have been
executed, and this draft does not itself change settlement configuration.

## Exclusions and zero effects

- Partner rebates received in October are ordinary October income, even where
  their commercial reference period begins earlier. They are outside this post.
- Spark's 75,063.88 October bridge CoF effect belongs in October's ordinary
  calculation. It is excluded from the January-September credit.
- Spark Ethena, BUIDL, USTB and USCC add no incremental January-September income
  correction. Complete earlier histories establish paid opening claims and no
  new 2026 async activity. Ordinary wallet yield remains included. The existing
  Spark JTRSY adapter is retained; Arkis's observed withdrawals settle atomically.
- Osero referral-code mappings 3014/3017 add zero January-September DR on the
  checked upstream source. No later receipts or unrelated source revisions are imported.
- The native-USDS bridge scan covers the four supported Ethereum routes.
  Grove's separate USDC returns do not establish a native-USDS credit; other
  bridge families are not implicitly certified by this calculation.

## Cause index

| PR | Historical correction |
|---|---|
| [#226](https://github.com/soterlabs/settlement-cycle/pull/226) | Grove async redemption and issued-deposit CoF |
| [#264](https://github.com/soterlabs/settlement-cycle/pull/264) | Grove Base vault and Diamond LP income |
| [#268](https://github.com/soterlabs/settlement-cycle/pull/268) | Grove small BUIDL capital/cash costs; warehouse/Basin reporting |
| [#267](https://github.com/soterlabs/settlement-cycle/pull/267) | Spark RLUSD receipts and investments |
| [#270](https://github.com/soterlabs/settlement-cycle/pull/270) | Osero executed duplicate-shortfall refund |
| [#274](https://github.com/soterlabs/settlement-cycle/pull/274) | Spark native-USDS bridge CoF |
| [#278](https://github.com/soterlabs/settlement-cycle/pull/278) | Spark/Obex Maple income; shared async ownership/capital follow-up |

The [PR inventory and calculation ledger](https://github.com/soterlabs/settlement-cycle/pull/277)
classify every merged PR in the comparison range, including documentation,
reporting-only changes and zero-amount referral-code changes. This post proposes
only the incremental historical corrections above; it does not restate rates
or claim to be a new complete monthly ALM transfer-review attestation.
