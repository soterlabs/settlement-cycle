# Distinguish execution costs from unidentified capital transfers

The post-Morpho-V1 diagnostic contains 14,546 unmatched outflow entries with a
historical gross value of **3,973,007.994839 USD**. This does not mean that the
same amount of capital went to unknown recipients, or that its monthly cost
of funds is unknown.

Reconstructing the previously reviewed Ethena, par-stable and sUSDS/USDT
executions, including the two legacy Curve pools described below, explains **3,061,046.951472 USD** of those entries. The audit checks
raw execution events against actual ALM cash and independently reconstructs
the sUSDS exchange rate. It handles transactions containing multiple execution
types and avoids counting already-recognized par-swap income twice.

The remaining signed difference is **911,961.043367 USD**; the sum of absolute
differences is **911,987.902816 USD**. Both are retained so positive and negative
valuation differences cannot silently cancel. A whole 700-dollar swap cost
should not remain unexplained just because its independently computed amount
differs from the snapshot by a fraction of a micro-dollar. Equally, the audit
does not round away that difference or assume it is necessarily rounding.

Of that remainder, **900,612.89 BUIDL** is already identified as the September 8,
2025 forwarding of interest mistakenly delivered to Spark after the Grove
portfolio sale. The [specific spell](https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20250904/SparkEthereum_20250904.sol)
and [forum rationale](https://forum.skyeco.com/t/september-4-2025-proposed-changes-to-spark-for-upcoming-spell/27102/1)
explain the recipient and purpose. Its monthly Sky-funded cost must be measured
from the ledger; it must not be estimated by charging the whole gift interest.

After separating that known forwarding, the unexplained absolute transaction
value is **11,375.012816 USD**, spread across smaller entries. This is the next
investigation set, not a measured loss or borrowing-cost discrepancy. Some
entries contain additional swaps not covered by the reviewed event sets.

`scripts/audit_spark_execution_residuals.py` produces the decomposition without
changing the replay, revenue, debt, or settlement amounts. It rejects altered
income registries and duplicate evidence identities. Where multiple residual
routes share one transaction, it leaves the whole-transaction witness unapplied
rather than counting it more than once. Six regression tests cover partial
matches, signed versus absolute differences, recognized income, ambiguous
routes, and invalid numeric inputs.

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_spark_execution_residuals.py \
  --residuals /tmp/pr215-spark-v1-static-residuals.json \
  --par-events tests/fixtures/spark_par_swap_events.json.gz \
  --susds-events tests/fixtures/spark_susds_curve_swaps.json.gz \
  --ethena-events tests/fixtures/spark_ethena_execution_events.json.gz \
  --income-rules config/capital-tracing/spark-par-swap-gains.json \
  --legacy-curve-events tests/fixtures/spark_legacy_curve_swaps.json.gz \
  --output /tmp/pr215-spark-v1-execution-residuals.json
```

The same command accepts a completed financing JSON (including gzip) through
`--residuals`. The current compact audit records the static-input scope and
hashes in `reconciliation/spark_execution_residual_decomposition_2026_08.json`.
Funded replay results will determine the actual August costs carried by these
accounts and their contribution to the global-cost comparison.

## Legacy Curve pools

The complete 2,527-log fixture authenticates **416 swaps** through block
25,878,704 in two older pools. Independently queried coin mappings agree at
the first observed swap and at the August pin:

- `0x4f493b7de8aac7d55f71853688b1f7c8f0243c85`: USDC / USDT.
- `0x383e6b4437b59fff47b619cba855ca29342a8559`: PYUSD / USDC.

For example, the [April 22 USDC/USDT swap](https://etherscan.io/tx/0x37500e81844741b9f17e933a4a1cf8f9b0fccd693130b723e734ef2b58afde19)
sends 500,000 USDC and receives 499,512.656668 USDT. The **487.343332**
difference exactly explains the corresponding outflow. The
[August 13 PYUSD swap](https://etherscan.io/tx/0x15ea32bf738b090d3bc2cb40efa2f3d778d9a60d91b55969bae10438d3d57625)
explains another **399.754883**.

Across all 416 transactions, negative execution differences total
**42,624.385402 USD** and positive differences total **2.123480 USD**.
Their contribution to the existing outflow list is **42,623.970880 USD**;
these totals differ because the outflow list does not contain every swap.
No additional income rule or funded-principal change is applied. Four more
regression tests cover real transactions, coin controls, cash mismatches, and
duplicate/conflicting logs.
