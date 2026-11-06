# June test withdrawals: two settled sUSDS legs, two pending USDS legs

On June 22, 2026, Spark withdrew 10,000 USDS and 10,000 sUSDS shares from each of
its Optimism and Unichain ALMs through their native bridges:

- [Optimism source](https://optimistic.etherscan.io/tx/0x8f10a77f52f7c583a8606120736ddd5766f6cb24027808d8f0238d856ca21d77)
- [Unichain source](https://uniscan.xyz/tx/0x6ba2c8158c8b1bc1e9daeafc5903f0d46978ccbb37d92eb80fea931582236625)

The source MessagePassed payloads authenticate the exact local/remote tokens,
raw amounts, source holders and Ethereum ALM recipient. Their payload and
withdrawal hashes are reconstructed from the raw events, not matched by amount
or a guessed time window.

| Route | Source USD value | Result at August closing pin |
|---|---:|---|
| Optimism USDS | 10,000.000000 | Pending |
| Optimism sUSDS | 11,006.223057 | July 2 arrival: 11,016.948001 USD |
| Unichain USDS | 10,000.000000 | Pending |
| Unichain sUSDS | 11,006.222909 | July 2 arrival: 11,016.947260 USD |

The sUSDS arrivals are [Optimism's finalization](https://etherscan.io/tx/0x9419f1d7242dfb1eb23fce5c4e1180c0de5dcad27580047edd8441658c1ac0ef)
and [Unichain's finalization](https://etherscan.io/tx/0xd476db3eb2c7e4e9fb08a1f9581e75bd265533be57166a238a44cef7df101c60).
Their matching RelayedMessage events, finalizations, and exact sUSDS escrow
payments prove delivery. The increase in sUSDS value carries no additional
borrowed principal.

Independent `successfulMessages()` and `finalizedWithdrawals()` calls at
Ethereum block **25878704** return false for both USDS legs and true for both
sUSDS legs. Thus **20,000 USDS remains in transit** at that pin. A missing
matching arrival alone is not the evidence for calling it pending.

The historical adapter now carries each token's own funding through separate
bridge claims, preserving the rest of these partially withdrawn positions.
The two pending USDS claims have no idle-cash exemption. This extends the
existing July full-exit mechanism without changing July's routes or evidence.
A later USDS finalization must be authenticated before closing those claims.

`tests/fixtures/spark_june_op_uni_withdrawals.json` stores the complete source
receipts, finalization logs, original normalized movements and pinned closing
calls. Tests verify all four messages and cash legs, retained pending claims,
partial-position basis, normal adapter dispatch and idempotency. No debt,
published revenue, API data or settlement report is changed.
