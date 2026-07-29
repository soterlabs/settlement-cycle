# SKY_TOTAL — 2026-05

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **25373623** (2026-06-22 14:04 UTC) — the single atomic settlement transaction for this cycle. MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 12,845,823.69 |
| Debt minted to buffer | grove | 8,877,823.00 |
| Debt minted to buffer | obex | 2,461,845.00 |
| Debt minted to buffer | **subtotal** | **24,185,491.69** |
| Sent to prime subproxy | spark | -4,204,857.00 |
| Sent to prime subproxy | grove | -271,843.00 |
| Sent to prime subproxy | obex | -526,204.00 |
| Sent to prime subproxy | keel | -32,279.00 |
| Sent to prime subproxy | skybase | -1,806,616.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-6,841,799.00** |
| Sent to Demand-side Buffer |  | -0.00 |
| Sent to Core Council | on-chain gross | -2,946,125.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +2,500,713.22 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-445,411.78** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **16,898,280.91** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,022,235.50 |
| non-MSC expense | -19,416,950.31 |
| **non-MSC net** | **-4,394,714.81** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 16,898,280.91 |
| non-MSC net | -4,394,714.81 |
| **Sky Net Revenue** | **12,503,566.09** |

> ⚠ grove_tge_penalty: no override for 2026-05 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
