# NON_MSC — 2026-02

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees at `vat.fold` (Art × Δrate); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (2026-03-09) | 8,895,647.88 |
| stability fee RWA001-A | 2,101,771.96 |
| stability fee ETH-C | 1,721,311.11 |
| stability fee LSEV2-SKY-A | 1,126,507.99 |
| stability fee ETH-A | 957,205.90 |
| stability fee RWA002-A | 137,595.69 |
| stability fee WSTETH-A | 129,506.35 |
| stability fee WSTETH-B | 90,315.14 |
| stability fee ETH-B | 45,724.36 |
| **total income** | **15,205,586.39** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 14,542,279.21 |
| less: prime-held sUSDS SSR — spark_alm (MSC-accounted) | -1,744,014.19 |
| less: prime-held sUSDS SSR — spark_alm_arbitrum (MSC-accounted) | -591,373.12 |
| less: prime-held sUSDS SSR — spark_psm3_base (MSC-accounted) | -376,822.12 |
| less: prime-held sUSDS SSR — spark_alm_optimism (MSC-accounted) | -304,649.00 |
| less: prime-held sUSDS SSR — spark_psm3_unichain (MSC-accounted) | -302,550.89 |
| less: prime-held sUSDS SSR — spark_alm_base (MSC-accounted) | -226,224.18 |
| less: prime-held sUSDS SSR — spark_psm3_optimism (MSC-accounted) | -182,348.98 |
| less: prime-held sUSDS SSR — spark_psm3_arbitrum (MSC-accounted) | -92,653.47 |
| less: prime-held sUSDS SSR — spark_alm_unichain (MSC-accounted) | -2,933.03 |
| sUSDS SSR to non-prime users | 10,718,710.23 |
| DSR (legacy pot) | 222,504.41 |
| stUSDS | 1,170,692.39 |
| **total expense** | **12,111,907.03** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **3,093,679.36** |
