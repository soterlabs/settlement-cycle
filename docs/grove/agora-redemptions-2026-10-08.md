# Grove Agora redemption capital links

The [January 29 spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260129/GroveEthereum_20260129.sol)
replaces the old Agora wallet with separate USDC mint/payment (`0x748b…677e`)
and AUSD redemption (`0xab83…fe67`) wallets. Actual ALM Transfer logs show
49 AUSD payments to the latter followed by 31 USDC receipts from the former.
Both tokens have **six decimals**.

There are 29 distinct, non-overlapping groups: $73,397,964.694525 AUSD sent,
$73,397,964.689863 USDC received. The only difference is $0.004662 on the
[April redemption](https://etherscan.io/tx/0xa67684111164714c459ef52fbb3418fa5f23c519096cc30f943c525c606914b9).
Several requests combine a small test payment with the larger payment. Some
receipts arrive the next day. These are explicitly reviewed historical links,
not a runtime heuristic that pairs unrelated transfers by amount or date.
The complete canonical boundary logs and exact transaction identities are
committed with the tests and adapter. No issuer-internal cash is traced.

The adapter preserves the original mix of borrowed and earned funding in a
pending redemption account assigned to E14. Receipt releases that existing
basis into USDC; only the final observed receipt realizes any shortfall. A
history pinned before payment keeps the claim outstanding. It cannot use a
future payment to close a current claim. Missing/changed normalized legs fail
validation. The shared implementation also retains the existing Ripple route
semantics and tests.

## August full-history replay

| Diagnostic | Before | After |
|---|---:|---:|
| Unmatched receipts | 186 | 155 |
| Unmatched outflows | 417 | 368 |
| Eligible allocation CoF | $1,233.51 | $1,233.51 |

The GROVE-A lower diagnostic CoF bound rises from $1,501.50 to **$2,565.99**;
its upper bound remains the $11,784.74 global control. BLOOM-A remains
unreconciled ($3,448,941.99 excluding MSC). Neither target being within the
remaining broad bounds establishes reconciliation. Unknown earlier funding
still prevents affected allocations from entering the eligible subtotal.

Evidence: `reconciliation/grove_agora_redemptions_2026_08.json`. Replay uses the
same saved normalized history, debt and idle controls as the preceding Ripple
checkpoint. Published settlements, revenue, API data and global CoF were not
modified. Validation: 172 allocation/Grove unit tests pass.
