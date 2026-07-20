# NON_MSC — 2026-02

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees on the accrual basis (Art × Δr_true, r_true reconstructed from `duty`); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (2026-03-09) | 8,895,647.88 |
| stability fee ETH-C | 1,717,774.82 |
| stability fee LSEV2-SKY-A | 1,135,223.63 |
| stability fee ETH-A | 958,442.11 |
| stability fee RWA002-A | 145,379.03 |
| stability fee WSTETH-A | 136,225.77 |
| stability fee WSTETH-B | 94,685.42 |
| stability fee RWA001-A | 52,593.51 |
| stability fee ETH-B | 47,987.42 |
| stability fee WBTC-C | 10,742.76 |
| stability fee RWA005-A | 10,122.00 |
| stability fee RWA004-A | 9,249.56 |
| stability fee WBTC-A | 8,471.97 |
| stability fee WBTC-B | 2,155.92 |
| **total income** | **13,224,701.81** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 14,542,279.21 |
| — of which: non-prime users (informational) | 10,718,710.23 |
| — of which: prime-held, spark_alm (offset by BR in MSC) | 1,744,014.19 |
| — of which: prime-held, spark_alm_arbitrum (offset by BR in MSC) | 591,373.12 |
| — of which: prime-held, spark_psm3_base (offset by BR in MSC) | 376,822.12 |
| — of which: prime-held, spark_alm_optimism (offset by BR in MSC) | 304,649.00 |
| — of which: prime-held, spark_psm3_unichain (offset by BR in MSC) | 302,550.89 |
| — of which: prime-held, spark_alm_base (offset by BR in MSC) | 226,224.18 |
| — of which: prime-held, spark_psm3_optimism (offset by BR in MSC) | 182,348.98 |
| — of which: prime-held, spark_psm3_arbitrum (offset by BR in MSC) | 92,653.47 |
| — of which: prime-held, spark_alm_unichain (offset by BR in MSC) | 2,933.03 |
| DSR (legacy pot) | 222,504.41 |
| stUSDS | 1,170,692.39 |
| **total expense** | **15,935,476.02** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **-2,710,774.20** |
