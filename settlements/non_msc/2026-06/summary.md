# NON_MSC — 2026-06

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees at `vat.fold` (Art × Δrate); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (none in window yet) | 0.00 |
| stability fee ETH-C | 2,185,116.90 |
| stability fee ETH-A | 1,209,713.96 |
| stability fee LSEV2-SKY-A | 1,047,487.76 |
| stability fee WSTETH-A | 145,270.44 |
| stability fee WSTETH-B | 58,038.13 |
| stability fee ETH-B | 42,113.33 |
| stability fee WBTC-A | 3,394.68 |
| **total income** | **4,691,135.20** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 17,504,764.66 |
| less: prime-held sUSDS SSR — spark_alm (MSC-accounted) | -3,923,041.52 |
| sUSDS SSR to non-prime users | 13,581,723.14 |
| DSR (legacy pot) | 222,412.90 |
| stUSDS | 1,037,444.16 |
| **total expense** | **14,841,580.20** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **-10,150,445.00** |

> ⚠ no jar burn found in (2026-06-30, 2026-07-31] at pin 25537330 — PSM income is $0 in this run; re-run after the monthly burn lands.
