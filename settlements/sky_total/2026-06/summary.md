# SKY_TOTAL — 2026-06

Consolidated Sky Net Revenue, buffer basis, defined to match Block Analitica's "Net revenue" dashboard line (operator decision 2026-08-06). Extracted from the MSC settlement block **25373623** (2026-06-22 14:04 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies (net of capital seedings). The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense. The FULL Core Council Buffer transfer (Step 1 Capital distribution + genesis/expense repayments = BA's "Security and Maintenance") sits BELOW net revenue, and the Grove TGE penalty is income Sky retains (already inside mint − subproxy) — neither reduces SNR.

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
| − Core Council Buffer transfer (BA: "Security and Maintenance") | -2,946,125.00 |
| &nbsp;&nbsp;of which: Step 1 Capital distribution (20% of cycle-month net, paid per MSC post) | -2,946,125.00 |
| &nbsp;&nbsp;of which: genesis / expense repayments | -0.00 |
| − capital seedings (one-off subproxy endowments) | -0.00 |
| **remitted to Sky reserves (known items only)** | **11,928,949.63** |

*BA's dashboard line additionally deducts buybacks ("Revenue Allocation"), the Aligned Delegates Buffer, and GAR (not tracked here).*
