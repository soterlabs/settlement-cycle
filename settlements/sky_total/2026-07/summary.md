# SKY_TOTAL — 2026-07

Consolidated Sky Net Revenue, **accrual basis** (from 2026-07 this repo computes both sides of the MSC ledger, so the report is derived from the per-prime settlement artifacts instead of the M+1 MSC settlement transaction — MSC operator decision 2026-08-04; 2026-01…2026-06 remain on the buffer basis). MSC net = Σ sky_revenue − Σ agent_rate − Σ distribution_rewards − Σ chronicle_points − Grove TGE penalty. Sky's payments to primes are exactly agent_rate + distribution_rewards + chronicle_points (Chronicle Points are Sky-funded); venue revenue is prime income from third parties, not a Sky expense. The figure is PRE Core-Council split.

## MSC leg (accrual basis)

| Prime | sky_revenue | − agent_rate | − distribution_rewards | − chronicle_points | net to Sky |
|---|---:|---:|---:|---:|---:|
| spark | 5,750,694.26 | -131,173.46 | -943,409.92 | 0.00 | 4,676,110.88 |
| grove | 8,003,550.33 | -72,023.77 | -28,898.24 | -13,102.08 | 7,889,526.24 |
| obex | 1,761,245.42 | -71,996.70 | 0.00 | 0.00 | 1,689,248.72 |
| keel | 0.00 | -32,003.53 | -4,227.11 | 0.00 | -36,230.64 |
| skybase | 0.00 | -37,629.25 | -95,303.38 | 0.00 | -132,932.63 |
| osero | 496.99 | -12,094.56 | -54.87 | 0.00 | -11,652.44 |
| **subtotal** | | | | | **14,074,070.14** |
| Grove TGE penalty (unset) | | | | | 0.00 |
| **MSC net (accrual basis)** | | | | | **14,074,070.14** |

## Non-MSC leg

| Line | USDS |
|---|---:|
| non-MSC income | 15,638,940.23 |
| non-MSC expense | -16,200,058.95 |
| **non-MSC net** | **-561,118.72** |

## Sky Net Revenue

| Field | USDS |
|---|---:|
| MSC net (accrual basis) | 14,074,070.14 |
| non-MSC net | -561,118.72 |
| **Sky Net Revenue** | **13,512,951.42** |

## Warnings

- ⚠ grove_tge_penalty: no override for 2026-07 in config/sky_total.yaml — treated as $0. Back-fill from the MSC forum post if a penalty applies to this month.
