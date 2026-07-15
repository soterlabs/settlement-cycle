# NON_MSC — 2026-06

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees at `vat.fold` (Art × Δrate); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (none in window yet) | 0.00 |
| stability fee ETH-C | 2,185,116.90 |
| stability fee ETH-A | 1,209,713.96 |
| stability fee LSEV2-SKY-A | 1,047,487.76 |
| stability fee RWA002-A | 201,039.51 |
| stability fee WSTETH-A | 145,270.44 |
| stability fee WSTETH-B | 58,038.13 |
| stability fee ETH-B | 42,113.33 |
| stability fee WBTC-A | 3,394.68 |
| **total income** | **4,892,174.71** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 17,504,764.66 |
| less: prime-held sUSDS SSR — spark_alm (MSC-accounted) | -3,923,041.52 |
| less: prime-held sUSDS SSR — spark_alm_arbitrum (MSC-accounted) | -418,498.23 |
| less: prime-held sUSDS SSR — spark_alm_optimism (MSC-accounted) | -393,316.31 |
| less: prime-held sUSDS SSR — spark_alm_base (MSC-accounted) | -352,243.28 |
| less: prime-held sUSDS SSR — spark_psm3_base (MSC-accounted) | -259,745.35 |
| less: prime-held sUSDS SSR — spark_psm3_unichain (MSC-accounted) | -199,933.97 |
| less: prime-held sUSDS SSR — spark_psm3_arbitrum (MSC-accounted) | -198,643.95 |
| less: prime-held sUSDS SSR — spark_psm3_optimism (MSC-accounted) | -185,384.85 |
| less: prime-held sUSDS SSR — spark_alm_unichain (MSC-accounted) | -103,007.63 |
| sUSDS SSR to non-prime users | 11,470,949.56 |
| DSR (legacy pot) | 222,412.90 |
| stUSDS | 1,037,444.16 |
| **total expense** | **12,730,806.62** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **-7,838,631.90** |

> ⚠ no jar burn found in (2026-06-30, 2026-07-31] at pin 25537523 — PSM income is $0 in this run; re-run after the monthly burn lands.
