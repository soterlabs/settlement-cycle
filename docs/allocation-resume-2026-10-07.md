# PR215 resumed: principal-return classification

The September23 custody replay remains the last full Spark/Grove reconciliation baseline. Neither prime reconciles, and this PR stays draft. The small reported cost subtotals exclude allocations with unresolved funding; they are not estimates of their economic borrowing costs.

## First correction

The capital normalizer now applies configured principal-return exceptions before labeling par-stable receipts from external revenue sources as income. Matching uses the primary ALM's daily net transfers, scoped to chain, token, counterparty, date and the configured one-dollar tolerance. It deduplicates logs before totaling. An override removes only the income label: the original custody/funding link is still required before any borrowed principal can be assigned.

Verified from canonical USDC Transfer logs, three configured Anchorage exceptions match:

| Date | Amount | Transaction |
|---|---:|---|
| 2026-05-14 | 5,270,830 | [receipt](https://etherscan.io/tx/0x1d3dd0adf2b6ab8c1bc89998bc4e370a4549382cf73a16c968c94f49445ad667) |
| 2026-07-16 | 10,036,438 | [receipt](https://etherscan.io/tx/0xe74aeeb85c45d4ee617fb2c422f5ed36c4887202e2074194f0926af53cb1a1d6) |
| 2026-08-17 | 51,267,944 | [receipt](https://etherscan.io/tx/0x5b719b6e1f8d7ae0035edbe4d4832efa544a3d79474f86c2fbea5899fc4a67fe) |

Total removed from the tracer's income classification: $66,575,212. This follows existing config; it does not resolve the embedded principal/interest split in the July/August returns or change published revenue.

The fourth configured exception, December19 2025, does NOT match day-net semantics: a [$5m payment](https://etherscan.io/tx/0x93979f7a1116b61d99e4076143bfb579ac7571e97d12b085a71e9914ced2a2ea) and [$5m return](https://etherscan.io/tx/0x84be9779551b28575c20ccfb447291f048aabf70d7638a2038c84f5941c9cbfe) net to zero. It needs a transaction-linked custody/return treatment, not a forced match of the current $5m day-net exception. The earlier notes listing four exceptions did not account for this distinction.

## Validation scope

37 focused capital-source, replay, financing and normalized-history tests pass. Ruff and git diff --check pass. Regression tests cover split receipts, same-day offsets, incorrect date/token/amount, and the requirement that unexplained principal returns must not manufacture borrowed funding.

A paired offline replay uses the September23 normalized snapshot, changing only the three verified receipt income fields. It retains original draws, transaction identities, amounts, timestamps and prices. Original normalized inputs and published settlement provenance are not overwritten. The previous standalone exact daily idle-input export is unavailable: both paired runs therefore omit that input and retain unavailable financing wherever the missing daily deduction matters. This comparison is a classification diagnostic, not a replacement for the previous full reconciliation.

## Next work

1. Integrate latest main without losing the existing August control; production now includes revenue/redemption fixes absent from the draft branch.
2. Trace Anchorage's outgoing funding and returned custody principal, including the verified December round trip. Fix earliest uncertainty-producing events and measure coverage of funded principal, not only transfer counts.
3. Reuse verified BUIDL request/cash links for Grove's pending custody, including exit fees; include its PAU positions and off-chain Galaxy funding. Preserve separate BLOOM-A/GROVE-A debt attribution.
4. Restore exact daily idle deductions and compare allocation costs plus explicitly identified financing components to per-ilk costs, with MSC-related costs separately identified. Keep global settlement charges unchanged.
5. Validate gross/net APY as a development diagnostic only; do not add publication warnings or gates. Do not substitute proportional report allocations for traced borrowed principal.
