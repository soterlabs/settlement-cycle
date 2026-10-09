# Remaining large receipt classifications

## Operator decisions, 2026-10-09

The operator confirmed the USDe Safe, Maple treasury, and additional PYUSD
payer receipts below as **yield**, totaling **10,193,206.19** at par. These
groups are no longer open classification questions. `config/spark.yaml` now
includes their senders; S30 enables the existing external-yield path. Historical
capital normalization recognizes their earned cash with no new borrowed basis.
The completed frozen replays predate this change and have not been rerun.

Settlement recognition activates in October 2026 to preserve the completed
reports through September. No historical true-up, report, or API update has
been generated. The regression test uses all 20 observed transfer logs and
checks earned cash, absence of new lender funding, and the activation boundary.

The three Spark Operations transfers below are **inflows to ALM proxies**.
The operator's conditional instruction to treat outflows as expenses therefore
does not classify these receipts. Their economic purpose remains open.

The findings below retain the investigation's original evidence and hypotheses;
the operator decisions above supersede their unconfirmed status for these
three approved payer groups.

This inventory isolates direct transfers at Spark's Ethereum ALM boundary.
It does not trace the subsequent or prior use of commingled counterparty funds.
The query covers genesis through the August 2026 pin, block 25,878,704, in both
directions for USDC, PYUSD and USDe. The raw 20-log result, query filters, token
decimals and addresses are retained in
`tests/fixtures/spark_unclassified_boundary_receipts.json.gz`.

| Counterparty | Receipt dates | Receipts | Total | Working hypothesis |
|---|---|---:|---:|---|
| Maple treasury `0x6a01c16eb312b80535f4799e4bf7522b715aacff` | 2025-10-20 | 2 | 1,661,609.59 USDC | Incentive or commercial payment; purpose unconfirmed |
| USDe distributing Safe `0xd0ec8cc7414f27ce85f8dece6b4a58225f273311` | 2025-05-19 through 2025-10-20 | 17 | 7,281,596.60 USDe | Recurring Ethena-related earnings/rebates; exact payment basis unconfirmed |
| PYUSD payer `0x1e30f9c2c688f85c82111d1d262bfd127e687282` | 2026-01-14 | 1 | 1,250,000 PYUSD | Reward or other commercial payment; payer/purpose unconfirmed |

All 20 transactions match the remaining receipt identities in the normalized
diagnostic. No reverse transfer was found for these counterparties in the three
tested stablecoins. This supports an income hypothesis but cannot establish it:
other assets, another sending wallet, or an off-chain principal settlement are
not excluded. Sender identity alone must not decide capital versus earnings.

The Maple payment consists of a 100-USDC test followed by the
[1,661,509.59-USDC payment](https://etherscan.io/tx/0xf47b94797d48eb81ceca66f8710eaa03bc5e89e40117a1b1fc5cda19094d6fe0).
Maple's [transparency filing](https://blockworks.com/token-transparency/filing/maple-finance/dc60bd1d-3554-4c92-9caf-82e8fc10d4c5)
identifies the treasury address; it does not explain this particular payment.

Most USDe receipts follow a weekly pattern, including
[904,142 USDe on August 7, 2025](https://etherscan.io/tx/0xa407dc48cca0eb9753b828825ab3c666b4cbe9360fc5bd14fde7e8b2757640e7).
The [August 2025 ecosystem accord proposal](https://forum.skyeco.com/t/26957/1)
discusses Spark/Grove sharing of Ethena-related net revenue, including rebates.
That supports the general economic explanation, but does not authenticate the
classification of each transfer from this Safe.

The [1.25m-PYUSD receipt](https://etherscan.io/tx/0x109c22ebdbdb3a975221ae7ea2b931bbae6fc40d0bcf6e37c0f4553b0c06dc05)
comes from a different address than the existing configured PayPal/Paxos reward
payer, `0xfc0539d019482d311c161ae3b756cdccdec45e87`. Do not silently extend that
revenue allowlist based on the token name.

These three groups total **10,193,206.19 USD at the tracer's par valuations**.
Together with the two separately modeled Anchorage receipts of 61,304,382 USD,
they concentrate 71,497,588.19 USD of the 74,069,476.39 USD historical unmatched
receipt total. These are historical cash amounts, not August revenue or costs.

No accounting classification or principal rule is changed by this inventory.
The replay does not create Sky borrowing from an unexplained receipt. If a
payment is confirmed as returned principal, it must be matched to the funded
allocation claim; if confirmed as earnings, it creates no borrowed basis.
The next useful evidence is a counterparty remittance/settlement explanation,
particularly the principal/interest split for Anchorage. Transaction amounts
alone cannot settle those questions.

## Separately confirmed: October 2025 Aave rewards

One additional receipt has a definite explanation: **243,167.543642328132
aUSDS**, received on October 8, 2025 in
[this transaction](https://etherscan.io/tx/0x0af39af528cd328028432e17f451aff046b19536824007f2e1665f1b7ed5b2e3).
The [October 2 Spark payload](https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20251002/SparkEthereum_20251002.sol#L180)
explicitly calls `claimAllRewardsToSelf` on the Aave incentives controller.
The receipt has a matching `RewardsClaimed` event naming the ALM as user,
recipient and claimer, plus an actual aUSDS transfer from the ACI distribution
wallet. The unrelated aToken interest mints are excluded.

`scripts/audit_spark_aave_reward_claim.py` verifies those two events and the
normalized receipt amount. This transaction has no new Sky draw, external
borrowing, or unexplained outgoing capital; its other positive movements are
already-recognized SparkLend reserve gifts. It is earned rewards, not a missing
capital deposit.

The running replay inputs are frozen and still list this receipt as unmatched.
This read-only proof explains its purpose without altering those checkpoints,
their uncertainty propagation, or published revenue. A later recognition update
can consume the authenticated claim. Four tests cover actual receipt evidence,
wrong recipients, missing delivery despite interest mints, and altered funding.

## Operations receipts and smaller unidentified payers

Three more receipts come from the same Spark Operations address,
`0x2e1b01adabb8d4981863394bea23a1263cbaedfc`, identified in the
[Spark Q2 report's address appendix](https://paragraph.com/@spark-11/spark-q2-2026-financial-report):

| Chain / date | USDC received | Transaction |
|---|---:|---|
| Base, 2025-12-03 | 779,893.018620 | [d24946…](https://basescan.org/tx/0xd24946f38a7bb9c627225c7c3de5e53ca44342bac264a9bf3a9a9eef01e63260) |
| Base, 2025-12-12 | 346,540.645157 | [d2f0a0…](https://basescan.org/tx/0xd2f0a0c9e7a016dc26609a48b54a95b6fc92c85d287069d15efadafb93a9e7e0) |
| Avalanche, 2026-03-17 | 76,629.483846 | [a452f9…](https://snowtrace.io/tx/0xa452f9c99ccc93d7dc6a7f48c059a65420dcb2dde331f8463edfe81bada8fcd5) |

Total: **1,203,063.147623 USDC**. The receipt proves payer and destination,
not the economic purpose. Possible explanations include proceeds from selling
reward tokens, an operational reimbursement, or returned capital. The operations
wallet's general liquidation role does not establish which applies here.

Three smaller Ethereum receipts also remain unclassified:

| Date | Amount | Payer | Transaction |
|---|---:|---|---|
| 2026-01-19 | 383,178.08 USDC | `0xc8a3e1e0776b912047c89dc16470fd9c7ea1141d` | [e8e9fa…](https://etherscan.io/tx/0xe8e9fa97ba936198cb147decccb93e24852503d25240f82f4bfc3612800eebed) |
| 2025-08-07 | 14,452.68561739 USDS | `0xaa2461f0f0a3de5feaf3273eae16def861cf594e` | [f6c5b0…](https://etherscan.io/tx/0xf6c5b04ec676db0b45a530552b97c7b4b09aab1777087b3b9d6fd8d8701c295e) |
| 2025-08-05 | 10,539.96 USDC | `0xcd531ae9efcce479654c4926dec5f6209531ca7b` | [cfd1fe…](https://etherscan.io/tx/0xcfd1fea5b70e95420e18f0f18c8528882db5bf235b93299f8f067499b2934a98) |

The six raw receipts and normalized batches are retained in
`tests/fixtures/spark_remaining_simple_receipts.json.gz`; the checked transfer
amounts and coordinates are in
`reconciliation/spark_remaining_simple_receipts_2026_08.json`. Each batch has
one incoming cash movement and no new Sky or Savings draw. This excludes a
missing draw in that same transaction; it does not exclude repayment of a
previously funded claim. These are targeted receipt checks, not complete
counterparty histories. No commingled wallet's other business is traced, and
no automatic income classification is introduced.
