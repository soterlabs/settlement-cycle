# SKY_TOTAL — 2026-01

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). No MSC settlement transaction executed in this calendar month (execution-month bucketing: each month carries the settlement that EXECUTED in it — the prior month's cycle), so the MSC leg is zero. MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 0.00 |
| Debt minted to buffer | grove | 0.00 |
| Debt minted to buffer | obex | 0.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **0.00** |
| Sent to prime subproxy | spark | -0.00 |
| Sent to prime subproxy | grove | -0.00 |
| Sent to prime subproxy | obex | -0.00 |
| Sent to prime subproxy | keel | -0.00 |
| Sent to prime subproxy | skybase | -0.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-0.00** |
| Sent to Demand-side Buffer |  | -0.00 |
| Sent to Core Council | on-chain gross | -0.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +0.00 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-0.00** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **0.00** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 25,259,561.59 |
| non-MSC expense | -16,040,847.78 |
| **non-MSC net** | **9,218,713.81** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 0.00 |
| non-MSC net | 9,218,713.81 |
| **Sky Net Revenue** | **9,218,713.81** |

> ⚠ grove_tge_penalty: no override for 2026-01 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
> ⚠ cc_genesis_repayment is NEGATIVE (-2,304,678.45) — the 20% Step 1 Capital rule (doc §3) doesn't hold for this cycle, or an outflow is unmodeled. Cross-check against BA's forum figure for MSC#2026-01 before treating this month's Sky Net Revenue as reconciled.
