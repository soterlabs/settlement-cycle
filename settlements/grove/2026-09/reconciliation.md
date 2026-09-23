# Grove reconciliation — September 2026

This is a dedicated reconciliation record, not a regenerated settlement.
Published 2026-01 through 2026-08 Grove reports are unchanged.

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
net-of-exit-cost yield are attributed entirely to Sky under this proposal.
Question G29 records the remaining policy confirmation: whether the custodian,
rather than the SDE owner, is contractually meant to bear the fee.

Measured reconciliation through the August closing boundary:

| Item | Amount |
|---|---:|
| May redemptions, measured but unbooked | $137,504.98 |
| August redemption settled before the boundary, measured but unbooked | $25,000.37 |
| **Published-period total, no restatement** | **$162,505.35** |
| August 31 redemption settled September 1, measured but unbooked | $12,499.85 |

Applying the 5 bps exit mark to the August E10 closing position of
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
not regenerated; the window is recorded for auditability and for future
partial redemptions that cross a settlement boundary.
