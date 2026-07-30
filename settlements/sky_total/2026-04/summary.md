# SKY_TOTAL — 2026-04

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **25072324** (2026-05-11 14:01 UTC) — the single atomic settlement transaction for this cycle. MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 8,781,143.27 |
| Debt minted to buffer | grove | 9,385,986.00 |
| Debt minted to buffer | obex | 1,969,499.00 |
| Debt minted to buffer | **subtotal** | **20,136,628.27** |
| Sent to prime subproxy | spark | -1,512,762.00 |
| Sent to prime subproxy | grove | -241,690.00 |
| Sent to prime subproxy | obex | -64,862.00 |
| Sent to prime subproxy | keel | -52,915.00 |
| Sent to prime subproxy | skybase | -201,469.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-2,073,698.00** |
| Sent to Demand-side Buffer |  | -0.00 |
| Sent to Core Council | on-chain gross | -3,144,308.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +3,391,981.33 |
| Sent to Core Council | of which: **genesis repayment (NEGATIVE — see warning)** | **-247,673.33** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **18,310,603.60** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 18,294,454.47 |
| non-MSC expense | -19,645,151.42 |
| **non-MSC net** | **-1,350,696.95** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 18,310,603.60 |
| non-MSC net | -1,350,696.95 |
| **Sky Net Revenue** | **16,959,906.65** |

> ⚠ grove_tge_penalty: no override for 2026-04 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
> ⚠ cc_genesis_repayment is NEGATIVE (-247,673.33) — the 20% Step 1 Capital rule (doc §3) doesn't hold for this cycle, or an outflow is unmodeled. Cross-check against BA's forum figure for MSC#2026-04 before treating this month's Sky Net Revenue as reconciled.
