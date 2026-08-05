# SKY_TOTAL — 2026-05

Consolidated Sky Net Revenue, buffer basis, defined to match Block Analitica's "Net revenue" dashboard line (operator decision 2026-08-06). Extracted from the MSC settlement block **25072324** (2026-05-11 14:01 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies (net of capital seedings). The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense. The FULL Core Council Buffer transfer (Step 1 Capital distribution + genesis/expense repayments = BA's "Security and Maintenance") sits BELOW net revenue, and the Grove TGE penalty is income Sky retains (already inside mint − subproxy) — neither reduces SNR.

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
| − Core Council Buffer transfer (BA: "Security and Maintenance") | -3,144,308.00 |
| &nbsp;&nbsp;of which: Step 1 Capital distribution (20% of cycle-month net, paid per MSC post) | -3,144,308.00 |
| &nbsp;&nbsp;of which: genesis / expense repayments | -0.00 |
| − capital seedings (one-off subproxy endowments) | -0.00 |
| **remitted to Sky reserves (known items only)** | **10,921,785.19** |

*BA's dashboard line additionally deducts buybacks ("Revenue Allocation"), the Aligned Delegates Buffer, and GAR (not tracked here).*
