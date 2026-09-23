# Spark reconciliation — September 2026

This is a dedicated reconciliation record, not a regenerated settlement.
The analysis is restricted to 2026-01 through 2026-08, inclusive. Those
published Spark reports are unchanged.

## SparkLend reserve-factor sweeps

SparkLend reserve treasuries distribute accrued reserve-factor income to the
Ethereum ALM in spUSDS, spUSDT, spUSDC, spPYUSD and spDAI. The position
balances already contained these receipts, but Cat C's scaled-balance formula
classified them as capital: the scaled-balance change cancels from pool yield.

From September 2026 onward, the two verified reserve-treasury senders are in
`external_alm_sources.ethereum`. The existing Cat C external-revenue path now
books their spToken receipts as Spark revenue, outside the SDE split. This is
additive to the supply APY, which is already net of the reserve factor.

The 2026 ALM receipts measured from `external_revenue` in each month's
provenance are:

| Month | Reserve-factor revenue |
|---|---:|
| 2026-01 | $187,229.81 |
| 2026-02 | $776,974.45 |
| 2026-03 | $192,240.53 |
| 2026-04 | $107,239.57 |
| 2026-05 | $58,633.73 |
| 2026-06 | $281,611.86 |
| 2026-07 | $317,345.18 |
| 2026-08 | $471,078.95 |
| **2026-01 through 2026-08** | **$2,392,354.07** |

The total is computed from unrounded provenance values. The independently
rounded monthly display rows sum to $2,392,354.08; the one-cent presentation
difference is not an additional adjustment.

## September reconciliation treatment

The $2,392,354.07 is recognized as prior-period **Prime-side Supply-Side
revenue**. Prime-side Demand-Side revenue, Sky-side Prime Cost of Funds,
Sky-side Direct Exposure revenue and Sky-side Supply-Side revenue are
unchanged.

The September settlement data carries `sv_adj: 2392354.07` for Spark. Under
the settlement formulas this increases both **MSC debt (mint)** and **Send to
prime** by $2,392,354.07 before whole-USDS rounding. Economically, Spark
receives the full 2026-01 through 2026-08 true-up while Sky's net revenue is
unchanged. The final integer mint/send values remain derived until the
September MSC post is published.

Control: these treasury senders were also checked for underlying par-stable
transfers to the ALM through the August closing pin; none were found. This
prevents the shared Cat A allowlist from reclassifying an underlying principal
movement as revenue.
