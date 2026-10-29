# Reserve earnings routed through SubProxy

The [March 12 Spark spell, section 4](https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20260312/SparkEthereum_20260312.sol#L111)
transfers the entire spDAI and spUSDS balances from SubProxy to the ALM. It
[executed March 14](https://etherscan.io/tx/0xd157dbc535da15f78cfb94eacbfbfe20c0b728f9f561350484919dfe499d239d).
Those transfers are additional to the same transaction's direct reserve-treasury
sweeps, already recognized by the earlier tracing adapter.

The complete SubProxy event history contains only twelve relevant events for
these two tokens. Integer scaled-balance reconstruction shows:

- A 1 USDS supply and a 1.1 DAI supply provide small initial seed balances.
- The September 8, 2025 treasury sweeps provide the large reserve-factor balances.
- Subsequent Mint events at transfer time represent accrued interest, not new
  scaled principal.
- The March 14 transfers exhaust the reconstructed positions. Independent
  `scaledBalanceOf(SubProxy)` calls confirm zero closing balances for both tokens.

Valued at the March execution's liquidity indices:

| Source | spDAI USD value | spUSDS USD value |
|---|---:|---:|
| Reserve treasury earnings and their index growth | 584,504.930671 | 633,099.293108 |
| Separately identified seed balances | 1.120075 | 1.029215 |

The tracing adapter therefore recognizes **1,217,604.223779 USD** as earned
funding. The **2.149291 USD** seed balance remains unclassified. It does not
assume all SubProxy cash is earned, classify a payer globally, or double-count
the direct treasury sweeps in this execution.

Raw evidence, closing contract calls and the transaction receipt are in
`tests/fixtures/spark_subproxy_reserve_history.json`. The independent audit is
`scripts/audit_spark_subproxy_reserves.py`, with its measured result in
`reconciliation/spark_subproxy_reserves_2026_03.json`. Tests reject a missing
seed event, reproduce the earned fractions, check unchanged asset/debt values,
and leave the small seed receipt visible in replay.

These are capital-tracing classifications. Published revenue, prior settlement
reports and API data are not restated.
