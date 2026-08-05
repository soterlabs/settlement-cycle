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
| Sent to prime subproxy | skybase | -10,000,000.00 |
| Sent to prime subproxy | — of which: one-off capital seeding (Vat.suck on vow; real cost) | 10,000,000.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-17,513,666.00** |
| Sent to Core Council | on-chain gross | -6,632,421.00 |
| Sent to Core Council | of which: Step 1 Capital (paid, per MSC post; add-back) | +5,845,338.00 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-787,083.00** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **23,327,147.00** |

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
| MSC net (buffer basis) | 23,327,147.00 |
| non-MSC net | -972,786.88 |
| **Sky Net Revenue** | **22,354,360.12** |

> ⚠ grove_tge_penalty: no override for 2026-02 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
> ⚠ one_off_transfers: excluding 10,000,000.00 USDS from 'skybase' subproxy (config/sky_total.yaml → one_off_transfers[2026-02][skybase])
