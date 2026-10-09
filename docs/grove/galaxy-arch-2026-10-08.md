# Galaxy ARCH CLO funding and cash returns

The [December 11 Grove spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251211/GroveEthereum_20251211.sol)
explicitly identifies `0x2e3a11807b94e689387f60cd4bf52a56857f2edc` as the
Galaxy ARCH CLO USDC deposit wallet. Actual ALM payments on December 16 total
**$49,900,000** ($1,000 test + $49,899,000), matching the GACLO token delivery.
The $50m configured notional schedule is not evidence of a $50m debt-funded
investment and is not used to manufacture the missing $100,000 of basis.

The four principal payments from `0x9dd1929124a9ad8d1bc7f029eebbbfeb0d898318`
match the corresponding GACLO reductions exactly:

| Cash received at Ethereum ALM | Principal returned | GACLO removed from Avalanche ALM |
|---|---:|---|
| May 1, 2026 | $3,591,655.48 | May 3 |
| June 12, 2026 | $18,721,388.15 | June 13 |
| August 10, 2026 | $4,729,780.41 | August 19 |
| August 14, 2026 | $4,949,341.17 | August 19 |

Thus $31,992,165.21 was returned, leaving **$17,907,834.79** outstanding. The
full cash/token evidence and transaction identities are committed in
`tests/fixtures/grove_galaxy_arch_events.json`. This is an off-chain payment
association corroborated by exact token reductions, not a cross-chain message
proof. Only those reviewed returns are included.

Per the operator's EOA-boundary policy, allocation capital starts at payment
to the authorized wallet and falls when principal cash arrives back at the
ALM. No tracing through commingled Galaxy accounts is needed. Waiting for the
later Avalanche token updates would count already-returned capital twice.
Likewise, the planned June 16 termination in a notional schedule cannot zero
capital that remains outstanding. The separate configured yield payer
`0xac3d…f1b` is not treated as a principal return.

The ARCH $1k test shares a transaction with the STAC test. The adapter retains
the existing STAC movement and consumes only ARCH's remaining funded amount.
The original borrowed/earned mix follows both deposits and principal returns.

## August replay

Unmatched receipts fall **57 → 53** and outflows **262 → 260**. E21 now has a
modeled average borrowed principal of **$20,715,421.12**, with modeled August
CoF **$64,225.02**. It remains **unresolved** because earlier funding and debt
repayments carry uncertainty; that amount is not a validated charge and is
excluded from the eligible subtotal. Both ilk controls remain unreconciled.

Evidence: `reconciliation/grove_galaxy_arch_2026_08.json`. Validation: 288
relevant tests pass. The patch changes capital tracing only; no published
report, API value, revenue amount or global borrowing cost is modified.
