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
| — of which: non-prime users (informational) | 11,409,770.64 |
| — of which: prime-held, spark_alm (offset by BR in MSC) | 1,735,361.42 |
| — of which: prime-held, spark_psm3_base (offset by BR in MSC) | 411,033.86 |
| — of which: prime-held, spark_psm3_unichain (offset by BR in MSC) | 329,073.27 |
| — of which: prime-held, spark_alm_arbitrum (offset by BR in MSC) | 253,240.86 |
| — of which: prime-held, spark_alm_base (offset by BR in MSC) | 249,659.53 |
| — of which: prime-held, spark_alm_optimism (offset by BR in MSC) | 132,252.66 |
| — of which: prime-held, spark_psm3_optimism (offset by BR in MSC) | 91,986.01 |
| — of which: prime-held, spark_psm3_arbitrum (offset by BR in MSC) | 52,316.32 |
| — of which: prime-held, spark_alm_unichain (offset by BR in MSC) | 3,236.88 |
| DSR (legacy pot) | 277,769.71 |
| stUSDS | 977,523.27 |
| **total expense** | **15,923,224.42** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **-928,620.10** |
