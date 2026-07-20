# NON_MSC — 2026-03

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees on the accrual basis (Art × Δr_true, r_true reconstructed from `duty`); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (2026-04-08) | 13,690,470.76 |
| stability fee ETH-C | 1,965,833.93 |
| stability fee ETH-A | 1,065,628.74 |
| stability fee LSEV2-SKY-A | 865,482.86 |
| stability fee RWA002-A | 172,592.12 |
| stability fee WSTETH-A | 146,150.71 |
| stability fee WSTETH-B | 93,121.09 |
| stability fee ETH-B | 55,038.12 |
| stability fee WBTC-C | 11,905.64 |
| stability fee RWA005-A | 11,246.44 |
| stability fee RWA004-A | 10,296.74 |
| stability fee WBTC-A | 8,797.34 |
| stability fee WBTC-B | 1,994.42 |
| **total income** | **18,098,558.93** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 19,513,274.75 |
| — of which: non-prime users (informational) | 14,330,466.42 |
| — of which: prime-held, spark_alm (offset by BR in MSC) | 2,998,729.24 |
| — of which: prime-held, spark_alm_arbitrum (offset by BR in MSC) | 489,316.27 |
| — of which: prime-held, spark_psm3_base (offset by BR in MSC) | 396,362.67 |
| — of which: prime-held, spark_psm3_unichain (offset by BR in MSC) | 324,078.26 |
| — of which: prime-held, spark_alm_optimism (offset by BR in MSC) | 323,368.97 |
| — of which: prime-held, spark_psm3_optimism (offset by BR in MSC) | 250,315.52 |
| — of which: prime-held, spark_alm_base (offset by BR in MSC) | 240,125.13 |
| — of which: prime-held, spark_psm3_arbitrum (offset by BR in MSC) | 157,399.01 |
| — of which: prime-held, spark_alm_unichain (offset by BR in MSC) | 3,113.26 |
| DSR (legacy pot) | 259,100.77 |
| stUSDS | 961,972.45 |
| **total expense** | **20,734,347.97** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **-2,635,789.04** |
