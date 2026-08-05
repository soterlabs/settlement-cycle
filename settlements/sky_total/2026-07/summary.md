# SKY_TOTAL — 2026-07

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **25574490** (2026-07-20 14:21 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Core Council (genesis portion) − Grove TGE penalty. The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense, mirroring Block Analitica's P&L. The Core Council on-chain mint is GROSS; the Step 1 Capital slice (20% of the cycle month's net revenue, PAID figure from the MSC post) is added back and only the genesis/repayment remainder is a cost.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 16,923,682.00 |
| Debt minted to buffer | grove | 12,342,158.00 |
| Debt minted to buffer | obex | 3,450,783.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **32,716,623.00** |
| Sent to prime subproxy | spark | -9,746,443.00 |
| Sent to prime subproxy | grove | -2,328,332.00 |
| Sent to prime subproxy | obex | -1,519,539.00 |
| Sent to prime subproxy | keel | -77,284.00 |
| Sent to prime subproxy | skybase | -204,242.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (net of seedings)** | **-13,875,840.00** |
| Sent to Core Council | on-chain gross | -3,378,069.00 |
| Sent to Core Council | of which: Step 1 Capital (paid, per MSC post; add-back) | +2,742,939.00 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-635,130.00** |
| Grove TGE penalty (excluded from Sky revenue) | config:2026-07 | -1,396,260.00 |
| **MSC net (buffer basis)** | | **16,809,393.00** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,638,940.23 |
| non-MSC expense | -19,219,232.42 |
| Demand-side Buffer transfer (Operating, per BA classification) | -34,902.00 |
| **non-MSC net** | **-3,615,194.19** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 16,809,393.00 |
| non-MSC net | -3,615,194.19 |
| **Sky Net Revenue** | **13,194,198.81** |

## Below the line (toward "remitted to Sky reserves")

| Field | USDS |
|---|---:|
| Sky Net Revenue | 13,194,198.81 |
| − Step 1 Capital distribution (paid) | -2,742,939.00 |
| − capital seedings (one-off subproxy endowments) | -0.00 |
| **remitted to Sky reserves (known items only)** | **10,451,259.81** |

*BA's dashboard line additionally deducts buybacks, the Aligned Delegates Buffer, and GAR (not tracked here).*
