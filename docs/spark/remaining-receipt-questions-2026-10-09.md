# Remaining large receipt classifications

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
