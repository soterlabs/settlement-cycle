# Spark BUIDL subscription capital — April/May 2025

Seven separate, completed subscriptions paid **800,100,000 USDC** to the
spell-authorized issuer entrypoint and received **799,525,374.43 BUIDL**.
The capital replay previously left the payments and delayed deliveries unlinked.

The [April 3, 2025 Spark spell](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20250403/SparkEthereum_20250403.sol)
explicitly names `BUIDL_DEPOSIT = 0xd1917664be3fdaea377f6e8d5bf043ab5c3b1312`
and the BUIDL token. The actual payments come from Spark's Ethereum ALM
`0x1601843c5e9bc251a3272907010afa41fa18347e`. Each reviewed subscription's
issuance completed before the following payment. Other intervening BUIDL mints
are daily distributions and are not linked to these payments.

| Payment date (UTC) | USDC paid | BUIDL delivered | Delivery block |
|---|---:|---:|---:|
| April 7 | 100,000 | 100,000 | 22,218,905 |
| April 8 | 50,000,000 | 49,949,924.88 | 22,226,064 |
| April 9 | 150,000,000 | 149,850,149.85 | 22,233,234 |
| April 10 | 150,000,000 | 149,880,149.85 | 22,240,411 |
| April 11 | 150,000,000 | 149,865,149.85 | 22,247,584 |
| April 30 | 150,000,000 | 149,940,000 | 22,383,739 |
| May 1 | 150,000,000 | 149,940,000 | 22,390,880 |

The **574,625.57** cash/face difference stays in acquisition funding basis.
This evidence establishes the amount paid and delivered; it does not establish
that every dollar of the difference is a specific fee. No new revenue or loss
is booked by this tracing adapter, and no borrowed debt is retired at issuance.

The initial **100,000** test subscription was below the existing capital-mint
threshold and therefore normalized as income. Its actual preceding paid
subscription disproves that classification. The exact reviewed delivery is
corrected inside capital tracing only; normal distributions remain earned
funding, and published revenue is not restated by this change.

Implementation: `src/settle/compute/spark_buidl_subscriptions.py`. The pending
issuer claim belongs to S19 from actual payment until delivery. Borrowed basis
comes from the payment's existing funding, never from token receipt value.
No tracing of the deposit EOA's commingled interior is needed. Only the seven
pinned transaction pairs are recognized; runtime nearest-date/amount matching
is not used. Missing funding, changed amounts/blocks/income labels, duplicate
transactions and out-of-order deliveries fail closed. Cutoffs before delivery
retain the pending claim; repeated application is idempotent.

Evidence: `tests/fixtures/spark_buidl_subscription_events.json` records all
100 matching issuer-deposit payments and incoming BUIDL transfers through the
existing August pin. Regression tests verify actual token addresses, mint
senders, holders, units and chronology, preserved paid basis, pending custody,
unchanged ordinary distributions, and a subscription funded by earned cash
that must receive zero borrowed basis.

This is a historical tracing repair, not a settlement or API revenue update.
The full Spark reconciliation remains subject to the unresolved Savings V2
funding-source model and other gaps. The isolated subscription test is not
proof that Spark's total August allocation costs reconcile.
