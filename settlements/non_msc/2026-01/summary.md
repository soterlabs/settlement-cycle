# NON_MSC — 2026-01

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees on the accrual basis (Art × Δr_true, r_true reconstructed from `duty`); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (2026-02-09) | 10,451,164.98 |
| stability fee ETH-C | 1,787,929.08 |
| stability fee ETH-A | 1,124,691.62 |
| stability fee LSEV2-SKY-A | 995,659.19 |
| stability fee RWA002-A | 166,116.48 |
| stability fee WSTETH-A | 155,317.18 |
| stability fee RWA001-A | 126,056.54 |
| stability fee WSTETH-B | 125,324.90 |
| stability fee ETH-B | 36,003.78 |
| stability fee WBTC-C | 11,778.99 |
| stability fee RWA005-A | 11,166.71 |
| stability fee RWA004-A | 10,184.74 |
| stability fee WBTC-A | 9,928.26 |
| stability fee WBTC-B | 2,603.30 |
| stability fee GUNIV3DAIUSDC2-A | 1,369.72 |
| stability fee GUNIV3DAIUSDC1-A | 100.69 |
| **total income** | **15,015,396.15** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 14,667,931.44 |
| — of which: non-prime users (informational) | 11,409,770.64 |
| — of which: prime-held, spark_alm (offset by BR in MSC) | 1,735,361.42 |
| — of which: prime-held, spark_psm3_base (offset by BR in MSC) | 411,033.86 |
| — of which: prime-held, spark_psm3_unichain (offset by BR in MSC) | 329,073.27 |
| — of which: prime-held, spark_alm_arbitrum (offset by BR in MSC) | 253,240.86 |
| — of which: prime-held, spark_alm_base (offset by BR in MSC) | 249,659.53 |
| — of which: prime-held, spark_alm_optimism (offset by BR in MSC) | 132,252.66 |
| — of which: prime-held, spark_psm3_optimism (offset by BR in MSC) | 91,986.01 |
| — of which: prime-held, spark_psm3_arbitrum (offset by BR in MSC) | 52,316.32 |
| — of which: prime-held, spark_alm_unichain (offset by BR in MSC) | 3,236.88 |
| DSR (legacy pot) | 277,769.71 |
| stUSDS | 977,523.27 |
| **total expense** | **15,923,224.42** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **-907,828.26** |
