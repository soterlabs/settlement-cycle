# Spark reconciliation — September 2026

This is a dedicated reconciliation record, not a regenerated settlement.
Published 2025-09 through 2026-08 Spark reports are unchanged.

## SparkLend reserve-factor sweeps

SparkLend reserve treasuries distribute accrued reserve-factor income to the
Ethereum ALM in spUSDS, spUSDT, spUSDC, spPYUSD and spDAI. The position
balances already contained these receipts, but Cat C's scaled-balance formula
classified them as capital: the scaled-balance change cancels from pool yield.

From September 2026 onward, the two verified reserve-treasury senders are in
`external_alm_sources.ethereum`. The existing Cat C external-revenue path now
books their spToken receipts as Spark revenue, outside the SDE split. This is
additive to the supply APY, which is already net of the reserve factor.

Historical ALM receipts measured from raw transfers, but not booked or used to
restate published reports:

| Period | Measured, unbooked |
|---|---:|
| 2025-09-22 through 2025-12 | $786,263.50 |
| 2026-01 through 2026-08 | $2,392,354.07 |
| **Total** | **$3,178,617.57** |

The 2026 monthly raw-transfer total includes $471,078.95 in August. Historical
transfers to the SubProxy on 2025-09-08 are outside these ALM-only totals and
are not inferred into a settlement adjustment.

Control: these treasury senders were also checked for underlying par-stable
transfers to the ALM through the August closing pin; none were found. This
prevents the shared Cat A allowlist from reclassifying an underlying principal
movement as revenue.
