# Spark's Paxos PYUSD→USDC conversion boundary

The [February 26, 2026 Spark spell](https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20260226/SparkEthereum_20260226.sol#L36)
names `0x2f7BE67e11A4D621E36f1A8371b0a5Fe16dE6B20` as
`PAXOS_PYUSD_USDC`. Section 7 permits PYUSD transfers to it with a 5m maximum
and 200m/day replenishment rate. This explains the repeated 5m PYUSD payments;
it is not an unidentified investment deposit or missing Sky mint.

The cash returns arrive from the distinct Paxos payout wallet
`0x264bd8291fae1d75db2c5f573b07faa6715997b5`. Connecting that wallet to these
conversions is an explicitly reviewed closed-history association, not a common
on-chain request ID. The entrypoint is spell-proven; the cash association is
qualified in diagnostic funding provenance. It is not a payer-wide income rule.

## Complete boundary inventory through August

The raw filtered history through Ethereum block 25,878,704 contains:

| Flow | Count | Amount |
|---|---:|---:|
| ALM → authorized entrypoint, PYUSD | 287 | 417,761,317.838699 |
| Payout wallet → ALM, USDC | 287 | 417,761,305.460000 |
| Remaining nominal claim | | 12.378699 |

March 2 has a 100 PYUSD test and 99.98 USDC return. The remaining 286 conversions
occur in May. The last payment is May 29. The chronological claim never becomes
negative and peaks at 9,722,223.049230. There are no further qualifying flows
before the August pin.

For example, [this May 28 outflow](https://etherscan.io/tx/0x44621cd83eef75c411ed7ee1fd059db50102cf09ba0e3d2e1d9f20b3bd8330aa)
sends exactly 5m PYUSD to the authorized entrypoint. [A later receipt](https://etherscan.io/tx/0x3a94ff99f039680c1f810090f2ae22f8f68128ee9b8d8a26788542e4dc72755b)
returns 4,999,999.97 USDC from the payout wallet. The model tracks the aggregate
funded boundary, without claiming these two transactions share a request ID or
tracing inside the issuer's commingled wallets.

## Treatment

Transfers into the boundary carry their existing Sky/saver/earned funding mix.
Cash returned releases that funding proportionally. The 12.378699 shortfall is
left as an outstanding funded claim. It may be withdrawal costs or rounding,
but neither a fee write-off nor income is assumed to force closure.

The explicit historical adapter creates a tracing-only `S_PAXOS_PYUSD_USDC`
allocation. It does not modify published revenue, settlements, API data or Sky
debt. Its funding-association qualification propagates through later transfers.
New transactions outside the reviewed event set need their own evidence.

All 574 affected batches preserve their original movements, external funding
operations and debt fields. The adapter is idempotent and rejects changed cash,
missing transactions within an affected history and partial transformations.
Raw fixtures and the independent aggregate audit are in:

- `tests/fixtures/spark_paxos_pyusd_usdc_events.json`
- `reconciliation/spark_paxos_pyusd_usdc_2026_08.json`

After the Savings, mixed-deposit and Paxos input changes, the signed
transaction-value scan leaves **76,972,148.81 USD** of gross incoming residuals
and **4,921,995.31 USD** outgoing. These are inception-to-August transaction
amounts, not August revenue, missing debt or certified borrowing-cost results.
The full replay is required to measure their impact on funding attribution.
