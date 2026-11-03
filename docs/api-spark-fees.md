# Spark fee classification

The Spark annual statement defaults to `prime-management.spark-income.v3.6`.
Other primes retain v3.5. Existing selections continue to replay their original
policy and amounts. No MSC calculation or daily publication is changed.

| API line | Meaning | Accounting basis |
| --- | --- | --- |
| `fees.sparklend` | Known spUSDS, spUSDC, spUSDT, spDAI and spPYUSD transfers from the reviewed SparkLend reserve treasuries into Spark's Ethereum ALM; partial until the other two streams are integrated | Received in kind, valued by the retained source |
| `fees.sparklend.rlusd`, `fees.sparklend.usdg` | spRLUSD and spUSDG reserve receipts found in the final transfer audit; not present in the retained daily financial perimeter | Unavailable pending integration |
| `fees.curator` | Morpho fee income attributable to Spark | Unavailable until independently valued fee-share history is integrated |

The reserve treasuries are `0xb137e7d16564c81ae2b0c8ee6b55de81dd46ece5`
and `0x856900aa78e856a5df1a2665ee3a66b2487cd68f`; the recipient is
`0x1601843c5e9bc251a3272907010afa41fa18347e`. These addresses are already
listed in `config/spark.yaml`. The five underlying receipt metrics retain their
IDs, `rewards.lending.sparklend.*`, for source continuity. In v3.6 they are
children of SparkLend fees and excluded from allocation rewards and interest
income. The noninterest-income subtotal includes the fee subtotal exactly once.
Total net revenue is unchanged by this reclassification.

A complete treasury-to-ALM Transfer scan through Ethereum block 26334741 found
spRLUSD and spUSDG payments in addition to the five supported tokens. Accordingly,
the fee subtotal is partial and displays its supported `known_value`, rather
than certifying the five-token sum as the complete fee total. September's known
component is $399,369.65 and October's is $538,074.78. Integrating the missing
receipt ledgers is still required before this draft is ready for release.

Coverage starts September 2026, matching the reviewed daily financial policy.
Earlier months remain unavailable: January–August treasury income was handled
as a September settlement true-up, and must not be booked again by assuming it
was received in September. Current partial months follow the selected daily
source cutoff. An unreviewed source configuration leaves fee attribution
unavailable, with known receipts retained as unclassified distributions under
`rewards.unattributed_sparklend_receipts` so income is not lost. Update the
reviewed configuration fingerprints only after checking
the external-source allowlist and its effective dates.

## Morpho recipient checks

Direct contract calls on 3 November 2026 at Ethereum block 26334614 and Base
block 53461811 confirm these fee recipients. Percentages describe the fraction
of vault interest charged as performance fees, not APR or earned USD income.

| Venue | Recipient | Performance fee |
| --- | --- | --- |
| S10 Blue Chip USDC | Ethereum Spark ALM | 1% |
| S11 old Blue Chip USDT | Ethereum Spark ALM | 10% |
| S65 Blue Chip USDT | Ethereum Spark ALM | 10% |
| S12 DAI | Ethereum Spark ALM | 10% |
| S13 USDS | Ethereum Spark ALM | 10% |
| S34 Base USDC | Base Spark ALM | 10% |
| RLUSD top-up component | `0xbe6b7dca8d5fce23b07e0da9b01d466b95b3edf3` | 10% |

V1 vaults expose `feeRecipient()` and `fee()`; V2 vaults expose
`performanceFeeRecipient()` and `performanceFee()`. A reverting V1 getter on
a V2 vault is not evidence of zero fees. Both USDT vaults have zero management
fee. The RLUSD recipient differs from the ALM; its ownership has not been
attributed to Spark here.

Existing `normalize/allocation_morpho_fees.py` and historical fixtures authenticate
actual fee-share mints to Spark, so curator income must not be assumed zero.
A complete reported amount still needs historical mint valuation and a bridge
to the native vault-income calculation, to avoid counting the same return twice.

## Non-stable gains

This fee-classification slice does **not** publish new non-stable gain values.
The dashboard hides a gain row only when every elapsed month has a supported,
available zero with current evidence. Missing, estimated and partially known
zeroes remain visible, as do nonzero months whose year-to-date sum is zero.

The Ethereum ALM holds SparkLend spWETH; spETH is a separate Spark Savings vault.
Its depositor-funded exposure needs an explicit gross-asset versus net-Spark
accounting decision before price gains can be recognised. MORPHO holdings must
cover both Ethereum and Base. Current zero ETH/WETH/AVAX balances alone do not
prove zero historical gains, and token receipts or deposits are not price gains.
