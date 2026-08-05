# SKY_TOTAL — 2026-04

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **24971851** (2026-04-27 14:02 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Core Council (genesis portion) − Grove TGE penalty. The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense, mirroring Block Analitica's P&L. The Core Council on-chain mint is GROSS; the Step 1 Capital slice (20% of the cycle month's net revenue, PAID figure from the MSC post) is added back and only the genesis/repayment remainder is a cost.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 7,662,339.00 |
| Debt minted to buffer | grove | 6,290,684.00 |
| Debt minted to buffer | obex | 2,075,648.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **16,028,671.00** |
| Sent to prime subproxy | spark | -1,725,726.00 |
| Sent to prime subproxy | grove | -138,412.00 |
| Sent to prime subproxy | obex | -69,793.00 |
| Sent to prime subproxy | keel | -30,241.00 |
| Sent to prime subproxy | skybase | -225,299.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (net of seedings)** | **-2,189,471.00** |
| Sent to Core Council | on-chain gross | -678,176.00 |
| Sent to Core Council | of which: Step 1 Capital (paid, per MSC post; add-back) | +678,176.00 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-0.00** |
| Grove TGE penalty (excluded from Sky revenue) | config:none | -0.00 |
| **MSC net (buffer basis)** | | **13,839,200.00** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 18,294,454.47 |
| non-MSC expense | -19,645,151.42 |
| Demand-side Buffer transfer (Operating, per BA classification) | -0.00 |
| **non-MSC net** | **-1,350,696.95** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 13,839,200.00 |
| non-MSC net | -1,350,696.95 |
| **Sky Net Revenue** | **12,488,503.05** |

## Below the line (toward "remitted to Sky reserves")

| Field | USDS |
|---|---:|
| Sky Net Revenue | 12,488,503.05 |
| − Step 1 Capital distribution (paid) | -678,176.00 |
| − capital seedings (one-off subproxy endowments) | -0.00 |
| **remitted to Sky reserves (known items only)** | **11,810,327.05** |

*BA's dashboard line additionally deducts buybacks, the Aligned Delegates Buffer, and GAR (not tracked here).*
