# NON_MSC — 2026-06

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees on the accrual basis (Art × Δr_true, r_true reconstructed from `duty`); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (none in window yet) | 0.00 |
| stability fee ETH-C | 2,121,022.19 |
| stability fee ETH-A | 1,133,403.64 |
| stability fee LSEV2-SKY-A | 1,045,965.09 |
| stability fee RWA002-A | 201,032.40 |
| stability fee WSTETH-A | 147,861.91 |
| stability fee WSTETH-B | 53,446.52 |
| stability fee ETH-B | 44,018.77 |
| stability fee WBTC-C | 11,742.85 |
| stability fee RWA005-A | 11,004.41 |
| stability fee RWA004-A | 10,135.04 |
| stability fee WBTC-A | 8,547.75 |
| stability fee WBTC-B | 1,999.67 |
| **total income** | **4,790,180.24** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 17,504,764.66 |
| — of which: non-prime users (informational) | 11,470,949.56 |
| — of which: prime-held, spark_alm (offset by BR in MSC) | 3,923,041.52 |
| — of which: prime-held, spark_alm_arbitrum (offset by BR in MSC) | 418,498.23 |
| — of which: prime-held, spark_alm_optimism (offset by BR in MSC) | 393,316.31 |
| — of which: prime-held, spark_alm_base (offset by BR in MSC) | 352,243.28 |
| — of which: prime-held, spark_psm3_base (offset by BR in MSC) | 259,745.35 |
| — of which: prime-held, spark_psm3_unichain (offset by BR in MSC) | 199,933.97 |
| — of which: prime-held, spark_psm3_arbitrum (offset by BR in MSC) | 198,643.95 |
| — of which: prime-held, spark_psm3_optimism (offset by BR in MSC) | 185,384.85 |
| — of which: prime-held, spark_alm_unichain (offset by BR in MSC) | 103,007.63 |
| DSR (legacy pot) | 222,412.90 |
| stUSDS | 1,037,444.16 |
| **total expense** | **18,764,621.72** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **-13,974,441.48** |

> ⚠ no jar burn found in (2026-06-30, 2026-07-31] at pin 25553046 — PSM income is $0 in this run; re-run after the monthly burn lands.
