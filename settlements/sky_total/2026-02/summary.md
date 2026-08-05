# SKY_TOTAL — 2026-02

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **24369632** (2026-02-02 14:00 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 24,439,872.87 |
| Debt minted to buffer | grove | 14,311,822.00 |
| Debt minted to buffer | obex | 1,768,819.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **40,520,513.87** |
| Sent to prime subproxy | spark | -7,071,339.00 |
| Sent to prime subproxy | grove | -0.00 |
| Sent to prime subproxy | obex | -442,327.00 |
| Sent to prime subproxy | keel | -0.00 |
| Sent to prime subproxy | skybase | -10,000,000.00 |
| Sent to prime subproxy | — of which: one-off capital seeding (Vat.suck on vow; real cost) | 10,000,000.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-17,513,666.00** |
| Sent to Demand-side Buffer |  | -0.00 |
| Sent to Core Council | on-chain gross | -6,632,421.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +3,850,410.00 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-2,782,011.00** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **20,224,836.87** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,153,981.13 |
| non-MSC expense | -16,126,768.01 |
| **non-MSC net** | **-972,786.88** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 20,224,836.87 |
| non-MSC net | -972,786.88 |
| **Sky Net Revenue** | **19,252,049.98** |

> ⚠ grove_tge_penalty: no override for 2026-02 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
> ⚠ one_off_transfers: excluding 10,000,000.00 USDS from 'skybase' subproxy (config/sky_total.yaml → one_off_transfers[2026-02][skybase])
