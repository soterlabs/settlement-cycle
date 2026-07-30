# SKY_TOTAL — 2026-02

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **24570218** (2026-03-02 14:00 UTC) — the single atomic settlement transaction for this cycle. MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 7,729,005.14 |
| Debt minted to buffer | grove | 6,205,320.00 |
| Debt minted to buffer | obex | 2,095,775.00 |
| Debt minted to buffer | **subtotal** | **16,030,100.14** |
| Sent to prime subproxy | spark | -1,387,824.00 |
| Sent to prime subproxy | grove | -6,090.00 |
| Sent to prime subproxy | obex | -71,342.00 |
| Sent to prime subproxy | keel | -0.00 |
| Sent to prime subproxy | skybase | -0.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-1,465,256.00** |
| Sent to Demand-side Buffer |  | -0.00 |
| Sent to Core Council | on-chain gross | -4,808,248.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +2,195,952.32 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-2,612,295.68** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **11,952,548.46** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,153,981.13 |
| non-MSC expense | -16,126,768.01 |
| **non-MSC net** | **-972,786.88** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 11,952,548.46 |
| non-MSC net | -972,786.88 |
| **Sky Net Revenue** | **10,979,761.58** |

> ⚠ grove_tge_penalty: no override for 2026-02 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
