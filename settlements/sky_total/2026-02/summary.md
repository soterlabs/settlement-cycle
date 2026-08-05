# SKY_TOTAL — 2026-02

Consolidated Sky Net Revenue, buffer basis, defined to match Block Analitica's "Net revenue" dashboard line (operator decision 2026-08-06). Extracted from the MSC settlement block **24369632** (2026-02-02 14:00 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies (net of capital seedings). The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense. The FULL Core Council Buffer transfer (Step 1 Capital distribution + genesis/expense repayments = BA's "Security and Maintenance") sits BELOW net revenue, and the Grove TGE penalty is income Sky retains (already inside mint − subproxy) — neither reduces SNR.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 25,547,255.00 |
| Debt minted to buffer | grove | 14,311,822.00 |
| Debt minted to buffer | obex | 1,768,819.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **41,627,896.00** |
| Sent to prime subproxy | spark | -7,071,339.00 |
| Sent to prime subproxy | grove | -0.00 |
| Sent to prime subproxy | obex | -442,327.00 |
| Sent to prime subproxy | keel | -0.00 |
| Sent to prime subproxy | skybase | -0.00 |
| Sent to prime subproxy | — excluded: one-off capital seeding (below the line; on-chain send was 10,000,000.00) | (10,000,000.00) |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (net of seedings)** | **-7,513,666.00** |
| **MSC net (buffer basis)** | | **34,114,230.00** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,153,981.13 |
| non-MSC expense | -16,126,768.01 |
| Demand-side Buffer transfer (Operating, per BA classification) | -0.00 |
| **non-MSC net** | **-972,786.88** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 34,114,230.00 |
| non-MSC net | -972,786.88 |
| **Sky Net Revenue** | **33,141,443.12** |

## Below the line (toward "remitted to Sky reserves")

| Field | USDS |
|---|---:|
| Sky Net Revenue | 33,141,443.12 |
| − Core Council Buffer transfer (BA: "Security and Maintenance") | -6,632,421.00 |
| &nbsp;&nbsp;of which: Step 1 Capital distribution (20% of cycle-month net, paid per MSC post) | -5,845,338.00 |
| &nbsp;&nbsp;of which: genesis / expense repayments | -787,083.00 |
| − capital seedings (one-off subproxy endowments) | -10,000,000.00 |
| **remitted to Sky reserves (known items only)** | **16,509,022.12** |

*BA's dashboard line additionally deducts buybacks ("Revenue Allocation"), the Aligned Delegates Buffer, and GAR (not tracked here).*

> ⚠ one_off_transfers: excluding 10,000,000.00 USDS from 'skybase' subproxy (config/sky_total.yaml → one_off_transfers[2026-02][skybase])
