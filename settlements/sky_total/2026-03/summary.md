# SKY_TOTAL — 2026-03

Consolidated Sky Net Revenue, buffer basis, defined to match Block Analitica's "Net revenue" dashboard line (operator decision 2026-08-06). Extracted from the 2 MSC settlement blocks **24570218**, **24772796** — every settlement transaction executed in this calendar month, components summed (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies (net of capital seedings). The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense. The FULL Core Council Buffer transfer (Step 1 Capital distribution + genesis/expense repayments = BA's "Security and Maintenance") sits BELOW net revenue, and the Grove TGE penalty is income Sky retains (already inside mint − subproxy) — neither reduces SNR.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 15,826,021.00 |
| Debt minted to buffer | grove | 12,552,149.00 |
| Debt minted to buffer | obex | 4,044,197.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **32,422,367.00** |
| Sent to prime subproxy | spark | -2,652,956.00 |
| Sent to prime subproxy | grove | -11,720.00 |
| Sent to prime subproxy | obex | -137,061.00 |
| Sent to prime subproxy | keel | -0.00 |
| Sent to prime subproxy | — excluded: one-off capital seeding (below the line; on-chain send was 10,000,000.00) | (10,000,000.00) |
| Sent to prime subproxy | skybase | -203,134.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | — excluded: one-off capital seeding (below the line; on-chain send was 10,000,000.00) | (10,000,000.00) |
| Sent to prime subproxy | **subtotal (net of seedings)** | **-3,004,871.00** |
| **MSC net (buffer basis)** | | **29,417,496.00** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 13,303,732.59 |
| non-MSC expense | -20,880,323.41 |
| Demand-side Buffer transfer (Operating, per BA classification) | -0.00 |
| **non-MSC net** | **-7,576,590.82** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 29,417,496.00 |
| non-MSC net | -7,576,590.82 |
| **Sky Net Revenue** | **21,840,905.18** |

## Below the line (toward "remitted to Sky reserves")

| Field | USDS |
|---|---:|
| Sky Net Revenue | 21,840,905.18 |
| − Core Council Buffer transfer (BA: "Security and Maintenance") | -7,354,155.00 |
| &nbsp;&nbsp;of which: Step 1 Capital distribution (20% of cycle-month net, paid per MSC post) | -7,354,155.00 |
| &nbsp;&nbsp;of which: genesis / expense repayments | -0.00 |
| − capital seedings (one-off subproxy endowments) | -20,000,000.00 |
| **remitted to Sky reserves (known items only)** | **-5,513,249.82** |

*BA's dashboard line additionally deducts buybacks ("Revenue Allocation"), the Aligned Delegates Buffer, and GAR (not tracked here).*

> ⚠ one_off_transfers: excluding 10,000,000.00 USDS from 'keel' subproxy (config/sky_total.yaml → one_off_transfers[2026-03][keel])
> ⚠ one_off_transfers: excluding 10,000,000.00 USDS from 'osero' subproxy (config/sky_total.yaml → one_off_transfers[2026-03][osero])
