# Grove reconciliation — September 2026

This is a dedicated reconciliation record, not a regenerated settlement.
The historical correction covers January-August. The separately identified
September cash realization below is current-period revenue. Published
January-August Grove reports are unchanged.

## BUIDL redemption fee

BUIDL redemptions settle in USDC at approximately 99.95% of share face value.
At the former $1 mark, the share burn appeared with equal and opposite signs
in E10's value change and capital flow, while the smaller USDC receipt appeared
in a different venue. The 5 bps gap therefore disappeared between venues
instead of entering revenue.

The September methodology marks E10 with `nav_haircut_bps: 5`. Both position
value and share-denominated capital flows now use $0.9995 per share. This
closes the cancellation structurally and keeps the contractual rate in config.
Because E10 is a fixed Sky Direct Exposure, the transition markdown and future
net-of-exit-cost yield are attributed entirely to Sky. G29 records this
owner-bears-exit-cost decision as resolved.

Measured reconciliation for 2026-01 through the August closing boundary:

| Item | Amount |
|---|---:|
| May redemptions, measured but unbooked | $137,504.98 |
| August redemption settled before the boundary, measured but unbooked | $25,000.37 |
| **Published-period total, no restatement** | **$162,505.35** |

The August 31 redemption fee settled on September 1 and is therefore excluded
from this Jan-Aug reconciliation.

## September reconciliation treatment

The $162,505.35 realized-fee credit is applied as a reduction to **Sky-side
Direct Exposure revenue** and therefore to **Sky-side Supply-Side revenue**.
E10 remains 100% Sky Direct Exposure, so E10 Prime-side revenue remains $0;
Prime-side Demand-Side revenue is unchanged.

The August 31 in-flight redemption also overstated Sky's August Prime Cost of
Funds by $2,508.545902; the dedicated reconciliation rounds that credit to
$2,508.55. Combined with the realized fees, Grove's Jan-Aug credit is
$165,013.90.

The September settlement data carries `sky_adj: -165013.90` for Grove. This
reduces Grove's **MSC debt (mint)** by $165,013.90 before whole-USDS rounding;
it does not create a separate **Send to prime** payment. This is the settlement
expression of the credit to Grove for the Jan-Aug fees and excess CoF already
absorbed by its ALM.

This realized-fee credit is separate from the prospective exit-value mark
below: the former corrects fees incurred in Jan-Aug, while the latter recognizes
the embedded exit cost of shares still held at the August boundary.

The haircut has an explicit 2026-09-01 effective date. September's opening pin
therefore retains the former $1 mark while later valuations use $0.9995, so the
transition is recognized exactly once. Applying the 5 bps exit mark to the
August E10 closing position of
$643,254,421.77 produces a one-time September markdown of $321,627.21 and a
marked value of $642,932,794.56. This is prospective recognition of the
remaining position's embedded exit cost, not a rewrite of May or August.

The existing flat `$15,000` capital-operation fee is a separate rule. Its
round-amount detection is evaluated on the gross pre-haircut amount so the new
5 bps mark does not suppress that independent deduction.

## August 31 redemption in flight

24,999,000 shares left E10 on August 31 before the closing pin;
$24,986,500.153219 USDC arrived on September 1. The SDE config now supports
repeatable partial `in_flight_redemptions` for fixed exposures. It keeps this
receivable at its $24,986,500.50 settlement-basis value on August 31 and drops
it on the cash settlement date, without ending the still-active E10 SDE.

The $0.346781 difference between that configured 5 bps mark and cash received
is retained as measured rounding/settlement residue. The August artifact is
not regenerated; its $2,508.55 CoF effect rides the September `sky_adj`, while
the window remains recorded for reproducibility and future partial redemptions.

## September cash realization of the August 31 request

The unresolved 24,999,000-share request has a $24,999,000 carrying value:
the $0.9995 valuation policy was not active when those shares left E10.
The September 1 USDC receipt of $24,986,500.153219 therefore realizes a
$12,499.846781 cost ($12,499.85 to cents), wholly borne by Sky.

| Component | Credit to Grove |
|---|---:|
| January-August fees and CoF correction (unchanged) | $165,013.90 |
| September-settled August 31 redemption | $12,499.85 |
| **Combined historical and boundary credit** | **$177,513.75** |

The implementation recognizes the September cost in E10 revenue, before its
100% SDE split. Consequently `sky_adj` retains only the $165,013.90 historical
correction. Applying `sky_adj: -177513.75` as well would deduct the September
cost twice. The combined reduction in MSC mint is $177,513.75 before the
existing whole-USDS rounding; Send to prime is unchanged. This accounting
placement corrects the duplicate-booking risk in the literal PRD instruction.

Transaction identities and exact arithmetic are in
`reconciliation/2026-09/buidl_cash_settlement.json`. The $24,986,500.50 in-flight
SDE value and the $2,508.55 historical CoF credit remain unchanged.

A separate September 1 redemption of 25,000,000 shares illustrates the
prospective rule: its carrying value is already $24,987,500. Cash of
$24,987,499.668811 recognizes only a further $0.331189 variance. It is not
part of the $177,513.75 historical-plus-boundary figure above.

Across all 14 September cash settlements, those additional post-haircut
variances total $4.779767. Total September realization cost is $12,504.626548.
Including that additional current-period residue, the historical credit plus
all September cash realizations is $177,518.526548 ($177,518.53 to cents).
This excludes the separate transition markdown and other venue revenue.

The isolated September refresh also restores three verified 1,000-share
redemptions on September 9, 14 and 21 excluded by the $1M transfer filter.
Their combined $2,998.50 carrying value is capital, not a revenue loss. The
related cash variances are included in the cash-cost total above. After all E10 changes, September E10 revenue is
$733,817.500777, entirely Sky-direct. The smaller marked daily SDE value adds
$516.07959982 to Grove's borrowing cost, separately from redemption fees.
