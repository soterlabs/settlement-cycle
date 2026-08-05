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
| Sent to prime subproxy | keel | -10,000,000.00 |
| Sent to prime subproxy | — of which: one-off capital seeding (Vat.suck on vow; real cost) | 10,000,000.00 |
| Sent to prime subproxy | skybase | -203,134.00 |
| Sent to prime subproxy | osero | -10,000,000.00 |
| Sent to prime subproxy | — of which: one-off capital seeding (Vat.suck on vow; real cost) | 10,000,000.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-23,004,871.00** |
| Sent to Core Council | on-chain gross | -7,354,155.00 |
| Sent to Core Council | of which: Step 1 Capital (paid, per MSC post; add-back) | +7,354,155.00 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-0.00** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **9,417,496.00** |

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
| MSC net (buffer basis) | 9,417,496.00 |
| non-MSC net | -7,576,590.82 |
| **Sky Net Revenue** | **1,840,905.18** |

> ⚠ grove_tge_penalty: no override for 2026-03 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
> ⚠ one_off_transfers: excluding 10,000,000.00 USDS from 'keel' subproxy (config/sky_total.yaml → one_off_transfers[2026-03][keel])
> ⚠ one_off_transfers: excluding 10,000,000.00 USDS from 'osero' subproxy (config/sky_total.yaml → one_off_transfers[2026-03][osero])
