# SKY_TOTAL — 2026-04

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **24971851** (2026-04-27 14:02 UTC) — the single atomic settlement transaction executed in this month (execution-month bucketing, aligned with Block Analitica's P&L from 2026-08-05: month M carries cycle M−1's settlement). MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 7,330,204.01 |
| Debt minted to buffer | grove | 6,290,684.00 |
| Debt minted to buffer | obex | 2,075,648.00 |
| Debt minted to buffer | grove_pau | 0.00 |
| Debt minted to buffer | osero | 0.00 |
| Debt minted to buffer | **subtotal** | **15,696,536.01** |
| Sent to prime subproxy | spark | -1,725,726.00 |
| Sent to prime subproxy | grove | -138,412.00 |
| Sent to prime subproxy | obex | -69,793.00 |
| Sent to prime subproxy | keel | -30,241.00 |
| Sent to prime subproxy | skybase | -225,299.00 |
| Sent to prime subproxy | osero | -0.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-2,189,471.00** |
| Sent to Demand-side Buffer |  | -0.00 |
| Sent to Core Council | on-chain gross | -678,176.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +2,869,548.01 |
| Sent to Core Council | of which: **genesis repayment (NEGATIVE — see warning)** | **-2,191,372.01** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **15,698,437.02** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 18,294,454.47 |
| non-MSC expense | -19,645,151.42 |
| **non-MSC net** | **-1,350,696.95** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 15,698,437.02 |
| non-MSC net | -1,350,696.95 |
| **Sky Net Revenue** | **14,347,740.07** |

> ⚠ grove_tge_penalty: no override for 2026-04 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
> ⚠ cc_genesis_repayment is NEGATIVE (-2,191,372.01) — the 20% Step 1 Capital rule (doc §3) doesn't hold for this cycle, or an outflow is unmodeled. Cross-check against BA's forum figure for MSC#2026-04 before treating this month's Sky Net Revenue as reconciled.
