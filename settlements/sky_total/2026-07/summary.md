# SKY_TOTAL — 2026-07

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **25574490** (2026-07-20 14:21 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 16,923,682.00 |
| Debt minted to buffer | grove | 12,342,158.00 |
| Debt minted to buffer | obex | 3,450,783.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **32,716,623.00** |
| Sent to prime subproxy | spark | -9,746,443.00 |
| Sent to prime subproxy | grove | -2,328,332.00 |
| Sent to prime subproxy | obex | -1,519,539.00 |
| Sent to prime subproxy | keel | -77,284.00 |
| Sent to prime subproxy | skybase | -204,242.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-13,875,840.00** |
| Sent to Demand-side Buffer |  | -34,902.00 |
| Sent to Core Council | on-chain gross | -3,378,069.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +2,612,814.95 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-765,254.05** |
| Grove TGE penalty (excluded from Sky revenue) | config:2026-07 | -1,396,260.00 |
| **MSC net (buffer basis)** | | **16,644,366.95** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,638,940.23 |
| non-MSC expense | -19,219,232.42 |
| **non-MSC net** | **-3,580,292.19** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 16,644,366.95 |
| non-MSC net | -3,580,292.19 |
| **Sky Net Revenue** | **13,064,074.76** |
