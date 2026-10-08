# Spark: 650m of governance funding authenticated across three L2s

This PR #215 tracing checkpoint links two existing Sky debt draws to five actual
L2 ALM receipts. It does not create debt, recalculate revenue, change idle
exemptions, or restate published reports. The full August financing replay is
still pending; 650m is **paid capital traced**, not a borrowing-cost reduction.

| Execution | Paid principal | Destinations |
| --- | ---: | --- |
| February 24, 2025 | 300,000,000 USDS | Arbitrum: 100m USDS + 100m-cost sUSDS; Base: 100m-cost sUSDS |
| January 19, 2026 | 350,000,000 USDS | Arbitrum: 250m-cost sUSDS; Optimism: 100m-cost sUSDS |

The executed instructions are the [February 20 Spark spell](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20250220/SparkEthereum_20250220.sol)
and [January 15 Spark spell](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20260115/SparkEthereum_20260115.sol).
Both move the drawn USDS from the allocator buffer through Spark's **subproxy**,
then into the native bridge escrow. Ethereum ALM custody alone misses this route.

## Authentication, independently checked in the regression fixture

Source executions:

- [300m draw](https://etherscan.io/tx/0x395e70dfbb3b3a23fbfd0e7a4ad659c77302e2f5923606e006e981097cc27ef9), block 21,916,640.
- [350m draw](https://etherscan.io/tx/0x311bb97ca6fe9688c5dd235fcde093829720b0e1933ef57e616fc0703e1d90e3), block 24,269,286.

The fixture contains full RPC receipts for both sources, three Arbitrum ticket
creations and their successful redemption executions, the companion Base and
Optimism receipts, and the seven original normalized capital batches.

For Arbitrum, tests pair `InboxMessageDelivered` with canonical bridge
`MessageDelivered` by message number, verify the payload hash, and reconstruct
the Nitro `0x69` RLP transaction hash using the
[official SDK algorithm](https://github.com/OffchainLabs/arbitrum-sdk/blob/cbb96c6f7f84d71bdef65d0fd9d3d7275a236711/packages/sdk/src/lib/message/ParentToChildMessage.ts).
`ArbRetryableTx.RedeemScheduled` then identifies the actual L2 execution. Its mint
must match the message's token, recipient and exact units, and the source
subproxy's escrow transfer. Dates and similar amounts alone are not sufficient.

| Principal | Arbitrum ticket | Successful delivery |
| --- | --- | --- |
| 100m USDS | `0x73f2301cba450cd4e63755602fb844e37c334696ea6cd7fb349266a241c63541` | [delivery](https://arbiscan.io/tx/0xba14a84914c8179a53152425e487041c26338b844ef515ffda51970cc0beb162) |
| 100m-cost sUSDS | `0xc2e7ebd718c05e710c7dc0a2cfe7b2883abc3712ece853e561087c5e05e420e0` | [delivery](https://arbiscan.io/tx/0xa086a99da04a206f35137f8dd2224db63258a5f072b2eeed6af535461ddf3b2e) |
| 250m-cost sUSDS | `0xbe33d601404e1d82129b60d8163a24ff264e13e0bbe08465487ffd8ad2eaf6b5` | [delivery](https://arbiscan.io/tx/0x6ecac4fb59666b2697aa1617293d00c074bcb72ad5a0f42f3cdfc1e430005e14) |

The Base and Optimism legs independently reconstruct the canonical
`relayMessage` hash from `SentMessage` and `SentMessageExtension1`, find its
successful `RelayedMessage`, and check actual escrow/mint token units:

- [Base sUSDS](https://basescan.org/tx/0x5802288d01441f44240d25501156ef4d640d2bb385c14b5f0862bb607bb75f43).
- [Optimism sUSDS](https://optimistic.etherscan.io/tx/0x687246d6f335579ad8fc18d223ab8218031b01e305b3daab63d16ca632477b11).

## Funding treatment and limits

The adapter creates funded in-flight claims at the source draw and releases each
claim on its authenticated delivery. No arrival is used beyond the input cutoff.
No source draw means no inferred borrowed funding. Savings accretion between
wrapping and delivery remains unborrowed; the actual paid amounts determine basis.
Claims receive no new idle exemption. Source/destination shapes, timestamps,
blocks, asset accounts and debt ownership are guarded; conflicting snapshots fail.

The January execution also delivered 187,229.805718… of reserve-factor spTokens.
The reserve-gift adapter recognizes only those proven earned receipts first;
none of the 350m draw is assigned to them. A regression test uses the actual
saved combined execution, checks both L2 principals, and checks zero borrowed
basis for both gifts. Shared Grove executions remain untouched.

The four focused tests prove messages/cash, principal conservation, source-only
cutoffs, idempotency, unrelated-prime isolation and rejection of altered inputs.
Full-history results must still be measured; this does not resolve the separate
Savings V2 external-funding/refinancing model or USCC historical NAV questions.
