# SKY_TOTAL — 2026-06

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **25574490** (2026-07-20 14:21 UTC) — the single atomic settlement transaction for this cycle. MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 16,190,100.91 |
| Debt minted to buffer | grove | 12,342,158.00 |
| Debt minted to buffer | obex | 3,450,783.00 |
| Debt minted to buffer | **subtotal** | **31,983,041.91** |
| Sent to prime subproxy | spark | -9,746,443.00 |
| Sent to prime subproxy | grove | -2,328,332.00 |
| Sent to prime subproxy | obex | -1,519,539.00 |
| Sent to prime subproxy | keel | -77,284.00 |
| Sent to prime subproxy | skybase | -204,242.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-13,875,840.00** |
| Sent to Demand-side Buffer |  | -34,902.00 |
| Sent to Core Council | on-chain gross | -3,378,069.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +2,561,825.64 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-816,243.36** |
| Grove TGE penalty (excluded from Sky revenue) | config:2026-06 | -1,396,260.00 |
| **MSC net (buffer basis)** | | **15,859,796.55** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,881,199.53 |
| non-MSC expense | -18,931,867.90 |
| **non-MSC net** | **-3,050,668.37** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 15,859,796.55 |
| non-MSC net | -3,050,668.37 |
| **Sky Net Revenue** | **12,809,128.18** |
