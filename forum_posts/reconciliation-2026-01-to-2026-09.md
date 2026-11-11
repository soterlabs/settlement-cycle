> **Draft — not ready for booking:** the signed correction in PR #281 is included. This read-only component replay does not certify complete monthly asset controls.

# Settlement Reconciliation - MSC #5-#13 (January-September 2026)

Draft for the October 2026 earning cycle (MSC #14, paid in November).
October's ordinary settlement is presented separately.

We replayed the applicable corrections starting with [PR #220](https://github.com/soterlabs/settlement-cycle/pull/220)
through [`703c668`](https://github.com/soterlabs/settlement-cycle/commit/703c66863a0e99edf839658081026926f038c70b),
against the published January-September reports. The September baseline is
[`536f149`](https://github.com/soterlabs/settlement-cycle/commit/536f1492089ed82fbf6eaabb956f7a194b887d47).
Amounts already handled in the [previous reconciliation](https://forum.skyeco.com/t/settlement-reconciliation-msc-5-12-january-august-2026/28273)
are deducted. We do not sum isolated PR estimates or rebook earlier payments.

## Historical adjustment ledger

All amounts are USDS. Positive supply-side adjustments add income; negative
ones recover overstated income. Negative Sky-share adjustments reduce the
agent's debt mint. A send-only refund increases its transfer without new debt.

| Prime | Supply-side adjustment | Sky-share adjustment | Send-only refund |
|---|---:|---:|---:|
| Spark | 249,141.52 | 536,094.04 | 0.00 |
| Grove | 11,554.74 | -110,419.56 | 0.00 |
| Osero | 0.00 | 0.00 | 5,514.00 |
| Obex | -273,508.90 | 0.00 | 0.00 |

Components are rounded to cents individually, preserving previously approved
credits. Monthly detail retains unrounded values. The ordinary MSC applies its
existing supply-side floor and whole-USDS rounding to actual October figures.

## What changed

- **Aave/SparkLend, Spark/Grove/Osero (#220):** replay all 139 allocation-months
  using complete holder event histories and pinned archive balances at actual
  event boundaries. This replaces approximated daily boundaries and the earlier
  event-principal estimate. Current production yield formulas, rounding and
  clean-exit behavior are retained. Whole-prime CoF does not change; venue CoF
  attribution can change with the capital time series.
- **Spark partner receipts (#267):** recognize 1,373,208.00 of native RLUSD
  received in September, plus Sentora RLUSD investment income of 3,674.16.
  SparkLend RLUSD S70 is included once in the comprehensive lending replay,
  replacing its former standalone 2,400.00 correction.
- **Spark native-USDS wallet/bridge correction (#281):** 536,094.04 of
  additional historical debt mint, June-September, replacing the superseded
  255,869.48 transit credit. Compare verified wallets plus eligible transit
  against frozen deductions, preserving original daily charge conventions.
- **Spark/Obex async vaults (#278, implementing #263):** correct queued shares,
  paid cash and dated capital flows. Spark's correction is -41,042.54;
  Obex's July correction is -273,508.90. Global CoF is unchanged.
- **Grove async CoF (#226):** 78,530.06 net redemption credit, after the earlier
  2,508.55 credit, plus 35,889.68 for issued deposits. The deposit calculation
  is marginal to corrected redemptions, avoiding overlapping exemptions.
- **Grove small BUIDL exits (#268):** 4,000.18 Sky-side debit: restore 4,001.00
  of omitted principal, less 0.82 of remaining fee credit. Previously credited
  fees and September's recognized boundary costs are excluded.
- **Grove missing venues (#264):** 7,219.49 September income from Base
  Steakhouse, and 1,030.45 from Diamond PAU Uniswap (27.90 August; 1,002.55
  September). The LP has no USDS leg and creates no extra CoF exemption.
- **Grove Base cash capital and E12 weights (#280):** exact cash adds 13,769.34
  to historical income. Net the inherited June E23 published-boundary difference
  of -10,747.77 once, for +3,021.57 added to the preceding ledger. E43's earlier
  missing-venue credit is preserved once. E12 changes attribution only. This
  update replays only Grove's 17 active Base allocation-months; all other prime
  results remain at their previously verified calculation versions.
- **Osero duplicate shortfall deduction (#270):** compare corrected rounded
  debt mints/transfers against executed cashflows for July-September, combining
  the lending replay with the refund. Income absorbed by a still-negative
  supply-side share does not create a second transfer or refund.

### Exact lending replay

These are revenue changes before Osero's period-net payment treatment.
Spark's column includes the S70 missing-venue correction; it is not added again.

| Earning month | Spark income change | Grove income change | Osero income change |
|---|---:|---:|---:|
| 2026-01 | 221,360.79 | 742.91 | 0.00 |
| 2026-02 | -523,330.59 | 662.25 | 0.00 |
| 2026-03 | -349,876.70 | -1,121.93 | 0.00 |
| 2026-04 | 326,952.59 | 0.00 | 0.00 |
| 2026-05 | -269,058.60 | 0.00 | 0.00 |
| 2026-06 | -137,963.68 | 0.00 | 0.00 |
| 2026-07 | -228,004.44 | 0.00 | 35.81 |
| 2026-08 | -129,177.47 | 0.00 | 234.83 |
| 2026-09 | 2,400.00 | 0.00 | 0.00 |

### Osero payment netting

| Earning month | Income change | Corrected supply share | Corrected debt mint | Corrected transfer | Transfer increase vs executed |
|---|---:|---:|---:|---:|---:|
| 2026-07 | 35.81 | -70.88 | 497.00 | 12,149.00 | 106.00 |
| 2026-08 | 234.83 | -1,213.02 | 7,006.00 | 31,604.00 | 1,448.00 |
| 2026-09 | 0.00 | -3,959.60 | 76,824.00 | 31,621.00 | 3,960.00 |

## Prior credits and booking once

Spark's 2,392,354.07 reserve-factor credit and Grove's 165,013.90 BUIDL/CoF
credit were already handled. Their historical gross recognition and prior
payment net to zero. Dated September activation rules remain in place; these
credits are not deducted again from unrelated native-yield corrections.
September venue results already refreshed before publication are the baseline,
including Spark S1's already-recognized external receipts.

Grove's -78,530.06 historical Sky adjustment and Osero's 5,514.00 refund are
already configured for October. The full ledger above includes them once.
The following amounts remain to add to those existing October entries:

| Prime | Additional supply-side adjustment | Additional Sky-share adjustment | Additional send-only refund |
|---|---:|---:|---:|
| Spark | 249,141.52 | 536,094.04 | 0.00 |
| Grove | 11,554.74 | -31,889.50 | 0.00 |
| Osero | 0.00 | 0.00 | 0.00 |
| Obex | -273,508.90 | 0.00 | 0.00 |

## Effects on Sky and prime agents

The full historical ledger has the following linear effects relative to the
ordinary October settlement, before the final floor and whole-USDS rounding.
Debt-mint effects assume the combined supply-side share is above the mint floor.
Sky net means debt minted less transfers to agents.

| Prime | Debt mint change | Transfer change | Sky net change |
|---|---:|---:|---:|
| Spark | 785,235.56 | 249,141.52 | 536,094.04 |
| Grove | -98,864.82 | 11,554.74 | -110,419.56 |
| Osero | 0.00 | 5,514.00 | -5,514.00 |
| Obex | -273,508.90 | -273,508.90 | 0.00 |

## Scope and evidence

The [calculation package](https://github.com/soterlabs/settlement-cycle/pull/277)
contains retained source inputs, hashes, per-venue and monthly results, the PR
inventory, offline replay scripts and proposed **replacement** October values.
Those values preserve ordinary October receipts and do not add another layer
of historical credits. This draft changes no frozen report, production
configuration, database revision or executed payment.

Partner rebates received in October are ordinary October income and excluded
from this historical ledger. Spark's 75,063.88 October bridge CoF effect also
belongs in October's ordinary calculation. Warehouse/Basin presentation and
venue CoF reattribution create no separate whole-prime credit. Basin's September
activation remains dated. Osero referral codes 3014/3017 add no historical DR
on the checked source. The existing zero-effect async findings remain unchanged.

This is a read-only component comparison, not a new complete monthly ALM
transfer-review attestation. Original daily rates, NAVs, subsidy conventions and
applicable policy dates are preserved. The bridge audit covers its four
supported Ethereum routes; unrelated bridge families are not implicitly certified.
