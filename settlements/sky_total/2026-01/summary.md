# SKY_TOTAL — 2026-01

Consolidated Sky Net Revenue, buffer basis (methodology handoff 2026-07-16 §3). Extracted from the MSC settlement block **24369632** (2026-02-02 14:00 UTC) — the single atomic settlement transaction for this cycle. MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies − sent to Demand-side Buffer − sent to Core Council (genesis portion) − Grove TGE penalty. The Core Council on-chain mint is GROSS; the 20% Step 1 Capital distribution is carved out algebraically from Sky Net Revenue.

## MSC leg (buffer basis)

| Section | Line | USDS |
|---|---|---:|
| Debt minted to buffer | spark | 24,439,872.87 |
| Debt minted to buffer | grove | 14,311,822.00 |
| Debt minted to buffer | obex | 1,768,819.00 |
| Debt minted to buffer | **subtotal** | **40,520,513.87** |
| Sent to prime subproxy | spark | -7,071,339.00 |
| Sent to prime subproxy | grove | -0.00 |
| Sent to prime subproxy | obex | -442,327.00 |
| Sent to prime subproxy | keel | -0.00 |
| Sent to prime subproxy | skybase | -10,000,000.00 |
| Sent to prime subproxy | — of which: one-off capital seeding (Vat.suck on vow; real cost) | 10,000,000.00 |
| Sent to prime subproxy | **subtotal (raw)** | **-17,513,666.00** |
| Sent to Demand-side Buffer |  | -0.00 |
| Sent to Core Council | on-chain gross | -6,632,421.00 |
| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +6,398,285.17 |
| Sent to Core Council | of which: **genesis repayment (net cost)** | **-234,135.83** |
| Grove TGE penalty (excluded from Sky revenue) | unset | -0.00 |
| **MSC net (buffer basis)** | | **22,772,712.04** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 25,259,561.59 |
| non-MSC expense | -16,040,847.78 |
| **non-MSC net** | **9,218,713.81** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 22,772,712.04 |
| non-MSC net | 9,218,713.81 |
| **Sky Net Revenue** | **31,991,425.85** |

> ⚠ grove_tge_penalty: no override for 2026-01 in config/sky_total.yaml — booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; back-fill earlier months from the corresponding MSC forum posts.
> ⚠ one_off_transfers: excluding 10,000,000.00 USDS from 'skybase' subproxy (config/sky_total.yaml → one_off_transfers[2026-01][skybase])
