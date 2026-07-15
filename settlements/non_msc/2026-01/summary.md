# NON_MSC — 2026-01

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology: PSM income cash-recognized at the jar burn following month-end; stability fees at `vat.fold` (Art × Δrate); savings interest at `drip`, sUSDS net of the prime-held carve-out (MSC-accounted).

## Income

| Stream | USDS |
|---|---:|
| PSM/Coinbase jar burn (2026-02-09) | 10,451,164.98 |
| stability fee ETH-C | 1,899,392.63 |
| stability fee ETH-A | 1,140,630.02 |
| stability fee LSEV2-SKY-A | 1,005,641.89 |
| stability fee WSTETH-A | 165,119.41 |
| stability fee RWA002-A | 156,936.54 |
| stability fee WSTETH-B | 128,946.41 |
| stability fee ETH-B | 46,772.44 |
| **total income** | **14,994,604.31** |

## Expense

| Stream | USDS |
|---|---:|
| sUSDS SSR (gross, all holders) | 14,667,931.44 |
| less: prime-held sUSDS SSR — spark_alm (MSC-accounted) | -1,735,361.42 |
| less: prime-held sUSDS SSR — spark_psm3_base (MSC-accounted) | -411,033.86 |
| less: prime-held sUSDS SSR — spark_psm3_unichain (MSC-accounted) | -329,073.27 |
| less: prime-held sUSDS SSR — spark_alm_arbitrum (MSC-accounted) | -253,240.86 |
| less: prime-held sUSDS SSR — spark_alm_base (MSC-accounted) | -249,659.53 |
| less: prime-held sUSDS SSR — spark_alm_optimism (MSC-accounted) | -132,252.66 |
| less: prime-held sUSDS SSR — spark_psm3_optimism (MSC-accounted) | -91,986.01 |
| less: prime-held sUSDS SSR — spark_psm3_arbitrum (MSC-accounted) | -52,316.32 |
| less: prime-held sUSDS SSR — spark_alm_unichain (MSC-accounted) | -3,236.88 |
| sUSDS SSR to non-prime users | 11,409,770.64 |
| DSR (legacy pot) | 277,769.71 |
| stUSDS | 977,523.27 |
| **total expense** | **12,665,063.61** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **2,329,540.70** |
