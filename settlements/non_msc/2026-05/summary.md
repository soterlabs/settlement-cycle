# NON_MSC — 2026-05

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees at `vat.fold` (Art × Δrate); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (2026-06-11) | 10,644,203.21 |
| stability fee ETH-C | 2,037,068.96 |
| stability fee ETH-A | 1,101,413.66 |
| stability fee LSEV2-SKY-A | 982,797.98 |
| stability fee RWA002-A | 202,429.16 |
| stability fee WSTETH-A | 162,576.20 |
| stability fee WSTETH-B | 86,322.59 |
| stability fee ETH-B | 60,259.71 |
| stability fee WBTC-C | 24,333.00 |
| stability fee WBTC-A | 17,931.84 |
| stability fee WBTC-B | 4,080.51 |
| **total income** | **15,323,416.83** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 18,107,793.46 |
| less: prime-held sUSDS SSR — spark_alm (MSC-accounted) | -5,799,596.59 |
| less: prime-held sUSDS SSR — spark_alm_arbitrum (MSC-accounted) | -436,050.87 |
| less: prime-held sUSDS SSR — spark_psm3_base (MSC-accounted) | -388,441.38 |
| less: prime-held sUSDS SSR — spark_psm3_unichain (MSC-accounted) | -312,524.77 |
| less: prime-held sUSDS SSR — spark_alm_optimism (MSC-accounted) | -310,441.00 |
| less: prime-held sUSDS SSR — spark_psm3_optimism (MSC-accounted) | -281,223.00 |
| less: prime-held sUSDS SSR — spark_alm_base (MSC-accounted) | -230,525.17 |
| less: prime-held sUSDS SSR — spark_psm3_arbitrum (MSC-accounted) | -212,368.59 |
| less: prime-held sUSDS SSR — spark_alm_unichain (MSC-accounted) | -2,988.80 |
| sUSDS SSR to non-prime users | 10,133,633.30 |
| DSR (legacy pot) | 249,020.80 |
| stUSDS | 1,061,298.20 |
| **total expense** | **11,443,952.30** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **3,879,464.53** |
