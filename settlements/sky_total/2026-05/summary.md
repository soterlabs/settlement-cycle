# SKY_TOTAL — 2026-05

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **25072324** (2026-05-11 14:01 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Core Council (genesis portion) − Grove TGE penalty. The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense, mirroring Block Analitica's P&L. The Core Council on-chain mint is GROSS; the Step 1 Capital slice (20% of the cycle month's net revenue, PAID figure from the MSC post) is added back and only the genesis/repayment remainder is a cost.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 9,179,021.00 |
| Debt minted to buffer | grove | 9,385,986.00 |
| Debt minted to buffer | obex | 1,969,499.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **20,534,506.00** |
| Sent to prime subproxy | spark | -1,512,762.00 |
| Sent to prime subproxy | grove | -241,690.00 |
| Sent to prime subproxy | obex | -64,862.00 |
| Sent to prime subproxy | keel | -52,915.00 |
| Sent to prime subproxy | skybase | -201,469.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (net of seedings)** | **-2,073,698.00** |
| Sent to Core Council | on-chain gross | -3,144,308.00 |
| Sent to Core Council | of which: Step 1 Capital (paid, per MSC post; add-back) | +3,144,308.00 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-0.00** |
| Grove TGE penalty (excluded from Sky revenue) | config:none | -0.00 |
| **MSC net (buffer basis)** | | **18,460,808.00** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,022,235.50 |
| non-MSC expense | -19,416,950.31 |
| Demand-side Buffer transfer (Operating, per BA classification) | -0.00 |
| **non-MSC net** | **-4,394,714.81** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 18,460,808.00 |
| non-MSC net | -4,394,714.81 |
| **Sky Net Revenue** | **14,066,093.19** |

## Below the line (toward "remitted to Sky reserves")

| Field | USDS |
|---|---:|
| Sky Net Revenue | 14,066,093.19 |
| − Step 1 Capital distribution (paid) | -3,144,308.00 |
| − capital seedings (one-off subproxy endowments) | -0.00 |
| **remitted to Sky reserves (known items only)** | **10,921,785.19** |

*BA's dashboard line additionally deducts buybacks, the Aligned Delegates Buffer, and GAR (not tracked here).*
