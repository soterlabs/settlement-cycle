# SKY_TOTAL — 2026-06

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **25373623** (2026-06-22 14:04 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 13,427,874.00 |
| Debt minted to buffer | grove | 8,877,823.00 |
| Debt minted to buffer | obex | 2,461,845.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **24,767,542.00** |
| Sent to prime subproxy | spark | -4,204,857.00 |
| Sent to prime subproxy | grove | -271,843.00 |
| Sent to prime subproxy | obex | -526,204.00 |
| Sent to prime subproxy | keel | -32,279.00 |
| Sent to prime subproxy | skybase | -1,806,616.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-6,841,799.00** |
| Sent to Demand-side Buffer |  | -0.00 |
| Sent to Core Council | on-chain gross | -2,946,125.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +2,982,237.41 |
| Sent to Core Council | of which: **genesis repayment (NEGATIVE — see warning)** | **-36,112.41** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **17,961,855.41** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,881,199.53 |
| non-MSC expense | -18,931,867.90 |
| **non-MSC net** | **-3,050,668.37** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 17,961,855.41 |
| non-MSC net | -3,050,668.37 |
| **Sky Net Revenue** | **14,911,187.04** |

> ⚠ grove_tge_penalty: no override for 2026-06 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
> ⚠ cc_genesis_repayment is NEGATIVE (-36,112.41) — the 20% Step 1 Capital rule (doc §3) doesn't hold for this cycle, or an outflow is unmodeled. Cross-check against BA's forum figure for MSC#2026-06 before treating this month's Sky Net Revenue as reconciled.
