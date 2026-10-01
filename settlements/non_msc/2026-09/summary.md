# NON_MSC — 2026-09

Sky protocol P&L outside the prime-agent (MSC) perimeter. Methodology (handoff 2026-07-16): stability fees on the accrual basis (Art × Δr_true, r_true reconstructed from `duty`); PSM income at the jar burn's landing month (cash basis); liquidation revenue = Σ take.owe − Σ bark.due; surplus returns = join→vow moves not attributable to the PSM/RWA jar; savings interest on the accrual basis (including unpaid interest at the period boundaries; sUSDS gross, prime split informational); liquidation keeper incentives and Vest suckable payouts on the expense side.

## Income

| Section | Line | USDS |
|---|---|---:|
| Crypto Vaults | stability fee ETH-C | 1,345,966.00 |
| Crypto Vaults | stability fee ETH-A | 1,299,934.74 |
| Crypto Vaults | stability fee LSEV2-SKY-A | 789,384.56 |
| Crypto Vaults | stability fee WSTETH-A | 146,885.60 |
| Crypto Vaults | stability fee ETH-B | 45,918.87 |
| Crypto Vaults | stability fee WSTETH-B | 40,999.16 |
| Crypto Vaults | stability fee WBTC-C | 12,877.40 |
| Crypto Vaults | stability fee WBTC-A | 9,059.33 |
| Crypto Vaults | stability fee WBTC-B | 2,202.61 |
| Crypto Vaults | **subtotal** | **3,693,228.29** |
| Legacy RWA | stability fee RWA002-A | 140,643.65 |
| Legacy RWA | stability fee RWA005-A | 11,127.18 |
| Legacy RWA | stability fee RWA004-A | 10,309.36 |
| Legacy RWA | RWA jars (void) | 0.00 |
| Legacy RWA | **subtotal** | **162,080.20** |
| PSM | LitePSM jar burn (2026-09-08) | 11,040,852.86 |
| PSM | **subtotal** | **11,040,852.86** |
| Liquidations | liquidation revenue (Σowe 0.00 − Σdue 0.00) | 0.00 |
| Other | surplus return (2026-09-15) | 42,469.15 |
| Other | Gelato keeper surplus refund — recognized at protocol custody (2026-09-07) | 42,469.15 |
| Other | Gelato keeper surplus refund — remove cash recognition already accrued (2026-09-15) | -42,469.15 |
| **Total** | | **14,938,630.49** |

Refunds are recognized upon receipt in protocol custody. Subsequent surplus-buffer settlement clears that receivable; the negative adjustment prevents recognition twice.
- Gelato keeper surplus refund (recognition): [transaction](https://etherscan.io/tx/0x181d52604b1b4303637ceb67bf5de9e10b134a0ad38b694a69464f55eb8aad86)
- Gelato keeper surplus refund (settlement_offset): [transaction](https://etherscan.io/tx/0x806e361391d6f7b0c0ca98f200644bff27e002490c04fe55c5b27f9566359e10)

## Expense

| Section | Line | USDS |
|---|---|---:|
| Savings | sUSDS SSR (gross, all holders) | 13,183,785.29 |
| Savings | — of which: non-prime users (informational) | 13,183,785.29 |
| Savings | stUSDS | 839,073.49 |
| Savings | DSR (legacy pot) | 207,172.50 |
| Liquidations | keeper incentives (Σ coin, kicks + redos) | 0.00 |
| Vest | gross suckable payouts | 0.00 |
| **Total** | | **14,230,031.28** |

## Net

| Field | USDS |
|---|---:|
| **non-MSC net revenue** | **708,599.21** |
