# Grove Agora subscriptions

The counterpart to the reviewed Agora redemptions is cash paid to the mint
wallet authorized by the [January 29 spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260129/GroveEthereum_20260129.sol).
Thirty payments total **$96,850,000 USDC**; 21 subsequent deliveries total the
same AUSD amount, across 20 non-overlapping subscription groups.

This is an off-chain conversion. The pairing is inferred from the explicitly
authorized subscription destination, ordered ALM boundary transfers and exact
par settlement of each closed group; there is no on-chain message ID joining
the two legs. The committed manifest fixes the reviewed transactions, rather
than applying an amount/date matching rule to other or future transfers.

The payer/deliverer need not be the same address: USDC goes to `0x748b…677e`;
20 AUSD deliveries come from `0xbe009…ca4f`. The March 31 $999,000 delivery
comes from `0x080f…9cf5`, after the actual $999,000 subscription payment:

- [USDC payment](https://etherscan.io/tx/0x5e126ce12124617b3b96ba325f99bb105b6938b6823ba59544ca373a7665e409)
- [AUSD delivery](https://etherscan.io/tx/0x4c676cb5f6a2bce3d039713a8dab53264683f5f6f43fbc60bcbc6ad778e54782)

Do not label any other transfer from these delivery addresses as borrowed
capital or investigate their commingled internal funds. Each linked delivery
must consume the corresponding actual pending subscription. FalconX returns
are distinct and keep their existing E36 boundary treatment.

Pending subscriptions belong to E14 (AUSD), not the USDC cash venue. Borrowed
basis moves with capital; earned funds never acquire borrowed basis. Cutoffs
before delivery retain pending capital, and a missing funding leg fails.

## August replay

- Unmatched receipts: **155 → 134**.
- Unmatched outflows: **368 → 338**.
- GROVE-A lower diagnostic CoF bound: **$2,565.99 → $5,465.91**, against the
  unchanged $11,784.74 global cost. The upper bound remains $11,784.74.
- Eligible allocation subtotal remains $1,233.51 because earlier uncertainty
  still affects the traced holdings; neither ilk fully reconciles.

Evidence: `reconciliation/grove_agora_subscriptions_2026_08.json`; canonical
logs: `tests/fixtures/grove_agora_subscription_events.json`. All 195 relevant
allocation/Grove tests pass. Published settlements and API data are unchanged.
