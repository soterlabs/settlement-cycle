# SKY_TOTAL — 2026-05

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **25072324** (2026-05-11 14:01 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 8,781,143.27 |
| Debt minted to buffer | grove | 9,385,986.00 |
| Debt minted to buffer | obex | 1,969,499.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **20,136,628.27** |
| Sent to prime subproxy | spark | -1,512,762.00 |
| Sent to prime subproxy | grove | -241,690.00 |
| Sent to prime subproxy | obex | -64,862.00 |
| Sent to prime subproxy | keel | -52,915.00 |
| Sent to prime subproxy | skybase | -201,469.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-2,073,698.00** |
| Sent to Demand-side Buffer |  | -0.00 |
| Sent to Core Council | on-chain gross | -3,144,308.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +2,630,976.86 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-513,331.14** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **17,549,599.13** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,022,235.50 |
| non-MSC expense | -19,416,950.31 |
| **non-MSC net** | **-4,394,714.81** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 17,549,599.13 |
| non-MSC net | -4,394,714.81 |
| **Sky Net Revenue** | **13,154,884.32** |

> ⚠ grove_tge_penalty: no override for 2026-05 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
