# NON_MSC — 2026-03

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees at `vat.fold` (Art × Δrate); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (2026-04-08) | 13,690,470.76 |
| stability fee ETH-C | 1,929,635.49 |
| stability fee ETH-A | 1,031,424.45 |
| stability fee LSEV2-SKY-A | 872,580.16 |
| stability fee RWA002-A | 189,786.58 |
| stability fee WSTETH-A | 148,691.97 |
| stability fee WSTETH-B | 89,486.46 |
| stability fee WBTC-C | 55,752.42 |
| stability fee ETH-B | 49,449.79 |
| stability fee WBTC-A | 41,059.51 |
| stability fee WBTC-B | 9,331.54 |
| **total income** | **18,107,669.13** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 19,513,274.75 |
| less: prime-held sUSDS SSR — spark_alm (MSC-accounted) | -2,998,729.24 |
| less: prime-held sUSDS SSR — spark_alm_arbitrum (MSC-accounted) | -489,316.27 |
| less: prime-held sUSDS SSR — spark_psm3_base (MSC-accounted) | -396,362.67 |
| less: prime-held sUSDS SSR — spark_psm3_unichain (MSC-accounted) | -324,078.26 |
| less: prime-held sUSDS SSR — spark_alm_optimism (MSC-accounted) | -323,368.97 |
| less: prime-held sUSDS SSR — spark_psm3_optimism (MSC-accounted) | -250,315.52 |
| less: prime-held sUSDS SSR — spark_alm_base (MSC-accounted) | -240,125.13 |
| less: prime-held sUSDS SSR — spark_psm3_arbitrum (MSC-accounted) | -157,399.01 |
| less: prime-held sUSDS SSR — spark_alm_unichain (MSC-accounted) | -3,113.26 |
| sUSDS SSR to non-prime users | 14,330,466.42 |
| DSR (legacy pot) | 259,100.77 |
| stUSDS | 961,972.45 |
| **total expense** | **15,551,539.64** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **2,556,129.49** |
