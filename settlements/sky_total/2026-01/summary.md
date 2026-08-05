# SKY_TOTAL — 2026-01

Consolidated Sky Net Revenue, buffer basis, defined to match Block Analitica's "Net revenue" dashboard line (operator decision 2026-08-06). No MSC settlement transaction executed in this calendar month (execution-month bucketing: each month carries the settlement that EXECUTED in it — the prior month's cycle), so the MSC leg is zero. MSC net = Σ debt minted to buffer per prime − Σ sent to prime subproxies (net of capital seedings). The Demand-side Buffer transfer is paid inside the settlement tx but classified under the non-MSC leg as an Operating expense. The FULL Core Council Buffer transfer (Step 1 Capital distribution + genesis/expense repayments = BA's "Security and Maintenance") sits BELOW net revenue, and the Grove TGE penalty is income Sky retains (already inside mint − subproxy) — neither reduces SNR.

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
| Sent to prime subproxy | **subtotal (net of seedings)** | **-0.00** |
| **MSC net (buffer basis)** | | **0.00** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 25,259,561.59 |
| non-MSC expense | -16,040,847.78 |
| Demand-side Buffer transfer (Operating, per BA classification) | -0.00 |
| **non-MSC net** | **9,218,713.81** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (buffer basis) | 0.00 |
| non-MSC net | 9,218,713.81 |
| **Sky Net Revenue** | **9,218,713.81** |
