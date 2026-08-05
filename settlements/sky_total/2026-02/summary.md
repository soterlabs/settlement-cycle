# SKY_TOTAL — 2026-02

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **24369632** (2026-02-02 14:00 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Core Council (genesis portion) − Grove TGE penalty. The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense, mirroring Block Analitica's P&L. The Core Council on-chain mint is GROSS; the Step 1 Capital slice (20% of the cycle month's net revenue, PAID figure from the MSC post) is added back and only the genesis/repayment remainder is a cost.

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
| Sent to Core Council | on-chain gross | -6,632,421.00 |
| Sent to Core Council | of which: Step 1 Capital (paid, per MSC post; add-back) | +5,845,338.00 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-787,083.00** |
| Grove TGE penalty (excluded from Sky revenue) | config:none | -0.00 |
| **MSC net (buffer basis)** | | **33,327,147.00** |

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
| MSC net (buffer basis) | 33,327,147.00 |
| non-MSC net | -972,786.88 |
| **Sky Net Revenue** | **32,354,360.12** |

## Below the line (toward "remitted to Sky reserves")

| Field | USDS |
|---|---:|
| Sky Net Revenue | 32,354,360.12 |
| − Step 1 Capital distribution (paid) | -5,845,338.00 |
| − capital seedings (one-off subproxy endowments) | -10,000,000.00 |
| **remitted to Sky reserves (known items only)** | **16,509,022.12** |

*BA's dashboard line additionally deducts buybacks, the Aligned Delegates Buffer, and GAR (not tracked here).*

> ⚠ one_off_transfers: excluding 10,000,000.00 USDS from 'skybase' subproxy (config/sky_total.yaml → one_off_transfers[2026-02][skybase])
