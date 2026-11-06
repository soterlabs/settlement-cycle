# July Optimism/Unichain withdrawals and per-token funding preservation

The July 2 [Optimism](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20260702/SparkOptimism_20260702.sol)
and [Unichain](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20260702/SparkUnichain_20260702.sol)
spells executed on July 6, 2026 and returned the entire USDS and sUSDS holdings
to the Ethereum ALM. Four authenticated bridge finalizations completed July 13.

| Source | USDS returned | sUSDS value at withdrawal | sUSDS value at receipt |
|---|---:|---:|---:|
| Optimism | 100,304,256.59 | 201,207,650.96 | 201,346,048.97 |
| Unichain | 99,990,828.81 | 104,413,816.48 | 104,485,643.17 |

Values are USD from the existing normalized replay snapshot, rounded here.
The immutable raw share units are independently checked against the burns and
escrow payments; the value increase of those same sUSDS units creates no debt.
Total source value is **505,916,552.85 USD**. Funding basis comes from the
existing source positions, not these displayed values.

`tests/fixtures/spark_op_uni_withdrawals.json` contains the complete source
receipt logs and the four Ethereum finalization transactions. Regression tests
reconstruct each `MessagePassed` payload and withdrawal hash; verify the
canonical L1 messenger's matching `RelayedMessage`; require successful portal
finalization; and match the exact source burn and L1 escrow payment to the
configured source holder and Ethereum ALM. No amount/date proximity matching
is used. In-flight custody retains basis without an idle-USDS exemption.

A new unequal-funding test exposed an additional bug in the earlier Base
adapter: putting both USDS and sUSDS legs in one generic clearing transaction
pooled their borrowed/earned ratios. The tokens have individually authenticated
bridge paths, so they should not share funding attribution. Base, Optimism and
Unichain withdrawals now use separate synthetic replay batches for each token
leg, retaining original block/time ordering and the zero debt change. This also
prevents uncertainty in one token from spreading to an independently funded
other token. The Base regression uses fully Sky-funded USDS and wholly earned
sUSDS, then repeats with only USDS funding unknown.

Tests cover the entire seven-day custody period, source-only cutoffs, no new
borrowing from appreciation, exact no-ops for unrelated histories, and rejection
of wrong or partially transformed input. This remains a capital-tracing change;
no published settlement, API revenue or global borrowing charge is regenerated.

A separate read-only check ruled out the Grove CCTP v2 hypothesis for this Spark
snapshot: queries for v2 `DepositForBurn`/`MintAndWithdraw` at the canonical
messenger, filtered to each configured ALM, returned zero matching transactions
on Ethereum, Base, Arbitrum, Optimism, Unichain and Avalanche through their
existing August pins. That negative result is not a claim about later activity.
