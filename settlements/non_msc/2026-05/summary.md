# NON_MSC — 2026-05

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees on the accrual basis (Art × Δr_true, r_true reconstructed from `duty`); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (2026-06-11) | 10,644,203.21 |
| stability fee ETH-C | 2,106,760.46 |
| stability fee ETH-A | 1,109,372.26 |
| stability fee LSEV2-SKY-A | 982,307.11 |
| stability fee RWA002-A | 209,823.95 |
| stability fee WSTETH-A | 152,575.39 |
| stability fee WSTETH-B | 94,189.45 |
| stability fee ETH-B | 56,577.74 |
| stability fee WBTC-C | 12,221.15 |
| stability fee RWA005-A | 11,329.48 |
| stability fee RWA004-A | 10,413.83 |
| stability fee WBTC-A | 9,006.14 |
| stability fee WBTC-B | 2,049.44 |
| **total income** | **15,400,829.62** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 18,107,793.46 |
| — of which: non-prime users (informational) | 10,133,633.30 |
| — of which: prime-held, spark_alm (offset by BR in MSC) | 5,799,596.59 |
| — of which: prime-held, spark_alm_arbitrum (offset by BR in MSC) | 436,050.87 |
| — of which: prime-held, spark_psm3_base (offset by BR in MSC) | 388,441.38 |
| — of which: prime-held, spark_psm3_unichain (offset by BR in MSC) | 312,524.77 |
| — of which: prime-held, spark_alm_optimism (offset by BR in MSC) | 310,441.00 |
| — of which: prime-held, spark_psm3_optimism (offset by BR in MSC) | 281,223.00 |
| — of which: prime-held, spark_alm_base (offset by BR in MSC) | 230,525.17 |
| — of which: prime-held, spark_psm3_arbitrum (offset by BR in MSC) | 212,368.59 |
| — of which: prime-held, spark_alm_unichain (offset by BR in MSC) | 2,988.80 |
| DSR (legacy pot) | 249,020.80 |
| stUSDS | 1,061,298.20 |
| **total expense** | **19,418,112.46** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **-4,017,282.84** |
