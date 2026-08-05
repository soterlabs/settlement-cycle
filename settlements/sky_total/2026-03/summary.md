# SKY_TOTAL — 2026-03

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **24570218** (2026-03-02 14:00 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 7,729,005.14 |
| Debt minted to buffer | grove | 6,205,320.00 |
| Debt minted to buffer | obex | 2,095,775.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **16,030,100.14** |
| Sent to prime subproxy | spark | -1,387,824.00 |
| Sent to prime subproxy | grove | -6,090.00 |
| Sent to prime subproxy | obex | -71,342.00 |
| Sent to prime subproxy | keel | -0.00 |
| Sent to prime subproxy | skybase | -0.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-1,465,256.00** |
| Sent to Demand-side Buffer |  | -0.00 |
| Sent to Core Council | on-chain gross | -4,808,248.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +545,001.33 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-4,263,246.67** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **10,301,597.47** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 13,303,732.59 |
| non-MSC expense | -20,880,323.41 |
| **non-MSC net** | **-7,576,590.82** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 10,301,597.47 |
| non-MSC net | -7,576,590.82 |
| **Sky Net Revenue** | **2,725,006.65** |

> ⚠ grove_tge_penalty: no override for 2026-03 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
