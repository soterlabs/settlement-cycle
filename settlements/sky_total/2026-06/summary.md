# SKY_TOTAL — 2026-06

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **25373623** (2026-06-22 14:04 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Core Council (genesis portion) − Grove TGE penalty. The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense, mirroring Block Analitica's P&L. The Core Council on-chain mint is GROSS; the Step 1 Capital slice (20% of the cycle month's net revenue, PAID figure from the MSC post) is added back and only the genesis/repayment remainder is a cost.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 13,427,874.00 |
| Debt minted to buffer | grove | 8,877,823.00 |
| Debt minted to buffer | obex | 2,461,845.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **24,767,542.00** |
| Sent to prime subproxy | spark | -4,204,857.00 |
| Sent to prime subproxy | grove | -271,843.00 |
| Sent to prime subproxy | obex | -526,204.00 |
| Sent to prime subproxy | keel | -32,279.00 |
| Sent to prime subproxy | skybase | -1,806,616.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (net of seedings)** | **-6,841,799.00** |
| Sent to Core Council | on-chain gross | -2,946,125.00 |
| Sent to Core Council | of which: Step 1 Capital (paid, per MSC post; add-back) | +2,946,125.00 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-0.00** |
| Grove TGE penalty (excluded from Sky revenue) | config:none | -0.00 |
| **MSC net (buffer basis)** | | **17,925,743.00** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,881,199.53 |
| non-MSC expense | -18,931,867.90 |
| Demand-side Buffer transfer (Operating, per BA classification) | -0.00 |
| **non-MSC net** | **-3,050,668.37** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 17,925,743.00 |
| non-MSC net | -3,050,668.37 |
| **Sky Net Revenue** | **14,875,074.63** |

## Below the line (toward "remitted to Sky reserves")

| Field | USDS |
|---|---:|
| Sky Net Revenue | 14,875,074.63 |
| − Step 1 Capital distribution (paid) | -2,946,125.00 |
| − capital seedings (one-off subproxy endowments) | -0.00 |
| **remitted to Sky reserves (known items only)** | **11,928,949.63** |

*BA's dashboard line additionally deducts buybacks, the Aligned Delegates Buffer, and GAR (not tracked here).*
