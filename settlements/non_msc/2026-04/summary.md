# NON_MSC — 2026-04

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees at `vat.fold` (Art × Δrate); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (2026-05-14) | 10,265,611.43 |
| stability fee ETH-C | 1,961,250.55 |
| stability fee LSEV2-SKY-A | 1,068,390.07 |
| stability fee ETH-A | 1,009,068.26 |
| stability fee RWA002-A | 194,686.56 |
| stability fee WSTETH-A | 134,402.06 |
| stability fee WSTETH-B | 96,200.61 |
| stability fee ETH-B | 56,920.13 |
| **total income** | **14,786,529.67** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 18,304,617.83 |
| — of which: non-prime users (informational) | 12,163,798.30 |
| — of which: prime-held, spark_alm (offset by BR in MSC) | 4,063,315.84 |
| — of which: prime-held, spark_alm_arbitrum (offset by BR in MSC) | 429,939.08 |
| — of which: prime-held, spark_psm3_base (offset by BR in MSC) | 369,331.90 |
| — of which: prime-held, spark_psm3_unichain (offset by BR in MSC) | 307,653.86 |
| — of which: prime-held, spark_alm_optimism (offset by BR in MSC) | 306,089.78 |
| — of which: prime-held, spark_psm3_optimism (offset by BR in MSC) | 254,090.83 |
| — of which: prime-held, spark_alm_base (offset by BR in MSC) | 227,294.07 |
| — of which: prime-held, spark_psm3_arbitrum (offset by BR in MSC) | 180,157.25 |
| — of which: prime-held, spark_alm_unichain (offset by BR in MSC) | 2,946.90 |
| DSR (legacy pot) | 250,769.05 |
| stUSDS | 1,093,246.40 |
| **total expense** | **19,648,633.28** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **-4,862,103.61** |
