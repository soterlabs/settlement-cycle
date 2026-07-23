# SKY_TOTAL — 2026-02

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **24772796** (2026-03-30 20:26 UTC) — the single atomic settlement transaction for this cycle. MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 7,411,014.45 |
| Debt minted to buffer | grove | 6,346,829.00 |
| Debt minted to buffer | obex | 1,948,422.00 |
| Debt minted to buffer | **subtotal** | **15,706,265.45** |
| Sent to prime subproxy | spark | -1,265,132.00 |
| Sent to prime subproxy | grove | -5,630.00 |
| Sent to prime subproxy | obex | -65,719.00 |
| Sent to prime subproxy | keel | -10,000,000.00 |
| Sent to prime subproxy | skybase | -203,134.00 |
| Sent to prime subproxy | **subtotal** | **-11,539,615.00** |
| Sent to Demand-side Buffer |  | -0.00 |
| Sent to Core Council | on-chain gross | -2,545,907.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +161,989.14 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-2,383,917.86** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **1,782,732.59** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,153,981.13 |
| non-MSC expense | -16,126,768.01 |
| **non-MSC net** | **-972,786.88** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 1,782,732.59 |
| non-MSC net | -972,786.88 |
| **Sky Net Revenue** | **809,945.71** |

> ⚠ grove_tge_penalty: no override for 2026-02 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
