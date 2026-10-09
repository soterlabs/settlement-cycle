# Distinguish execution costs from unidentified capital transfers

The post-Morpho-V1 diagnostic contains 14,546 unmatched outflow entries with a
historical gross value of **3,973,007.994839 USD**. This does not mean that the
same amount of capital went to unknown recipients, or that its monthly cost
of funds is unknown.

Reconstructing the previously reviewed Ethena, par-stable and sUSDS/USDT
executions explains **3,018,422.980592 USD** of those entries. The audit checks
raw execution events against actual ALM cash and independently reconstructs
the sUSDS exchange rate. It handles transactions containing multiple execution
types and avoids counting already-recognized par-swap income twice.

The remaining signed difference is **954,585.014247 USD**; the sum of absolute
differences is **954,612.579232 USD**. Both are retained so positive and negative
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
value is **53,999.689232 USD**, spread across smaller entries. This is the next
investigation set, not a measured loss or borrowing-cost discrepancy. Some
entries contain additional swaps not covered by the three reviewed event sets.

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
  --output /tmp/pr215-spark-v1-execution-residuals.json
```

The same command accepts a completed financing JSON (including gzip) through
`--residuals`. The current compact audit records the static-input scope and
hashes in `reconciliation/spark_execution_residual_decomposition_2026_08.json`.
Funded replay results will determine the actual August costs carried by these
accounts and their contribution to the global-cost comparison.
