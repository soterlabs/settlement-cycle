# SKY_TOTAL — 2026-01

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). No MSC settlement transaction executed in this calendar month (execution-month bucketing: each month carries the settlement that EXECUTED in it — the prior month's cycle), so the MSC leg is zero. MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Core Council (genesis portion) − Grove TGE penalty. The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense, mirroring Block Analitica's P&L. The Core Council on-chain mint is GROSS; the Step 1 Capital slice (20% of the cycle month's net revenue, PAID figure from the MSC post) is added back and only the genesis/repayment remainder is a cost.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 0.00 |
| Debt minted to buffer | grove | 0.00 |
| Debt minted to buffer | obex | 0.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **0.00** |
| Sent to prime subproxy | spark | -0.00 |
| Sent to prime subproxy | grove | -0.00 |
| Sent to prime subproxy | obex | -0.00 |
| Sent to prime subproxy | keel | -0.00 |
| Sent to prime subproxy | skybase | -0.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (net of seedings)** | **-0.00** |
| Sent to Core Council | on-chain gross | -0.00 |
| Sent to Core Council | of which: Step 1 Capital (paid, per MSC post; add-back) | +0.00 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-0.00** |
| Grove TGE penalty (excluded from Sky revenue) | config:none | -0.00 |
| **MSC net (buffer basis)** | | **0.00** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 25,259,561.59 |
| non-MSC expense | -16,040,847.78 |
| Demand-side Buffer transfer (Operating, per BA classification) | -0.00 |
| **non-MSC net** | **9,218,713.81** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 0.00 |
| non-MSC net | 9,218,713.81 |
| **Sky Net Revenue** | **9,218,713.81** |
