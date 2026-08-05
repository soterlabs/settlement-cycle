# SKY_TOTAL — 2026-03

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the 2 MSC settlement blocks **24570218**, **24772796** — every settlement transaction executed in this calendar month, components summed (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Core Council (genesis portion) − Grove TGE penalty. The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense, mirroring Block Analitica's P&L. The Core Council on-chain mint is GROSS; the Step 1 Capital slice (20% of the cycle month's net revenue, PAID figure from the MSC post) is added back and only the genesis/repayment remainder is a cost.

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
| Sent to Core Council | on-chain gross | -7,354,155.00 |
| Sent to Core Council | of which: Step 1 Capital (paid, per MSC post; add-back) | +7,354,155.00 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-0.00** |
| Grove TGE penalty (excluded from Sky revenue) | config:none | -0.00 |
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
| − Step 1 Capital distribution (paid) | -7,354,155.00 |
| − capital seedings (one-off subproxy endowments) | -20,000,000.00 |
| **remitted to Sky reserves (known items only)** | **-5,513,249.82** |

*BA's dashboard line additionally deducts buybacks, the Aligned Delegates Buffer, and GAR (not tracked here).*

> ⚠ one_off_transfers: excluding 10,000,000.00 USDS from 'keel' subproxy (config/sky_total.yaml → one_off_transfers[2026-03][keel])
> ⚠ one_off_transfers: excluding 10,000,000.00 USDS from 'osero' subproxy (config/sky_total.yaml → one_off_transfers[2026-03][osero])
