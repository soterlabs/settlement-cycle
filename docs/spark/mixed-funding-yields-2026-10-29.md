# Spark mixed-funding yields: daily attribution workstream

This implements the next step in [the funding PRD](../PRD_allocation_funding_next_steps.md). It does not change settlement amounts or publish a guessed funding split.

## What changed

The retained capital ledger can now optionally export sparse end-of-day snapshots for selected allocation accounts. Each snapshot keeps Sky principal, Savings principal by vault, last observed value and funding uncertainty separately. Snapshot dictionaries are copied, so a later repayment cannot rewrite earlier days.

The existing model already handles Sky refinancing a Savings repayment without a new asset deposit. It also carries principal through withdrawals, bridges and custody, keeps gains out of principal and puts VSR interest in a separate financing-expense account. Those rules are reused, not reimplemented. The accepted provisional policy and the precise May 18, 2026 repayment example remain documented in [Savings principal policy](savings-principal-policy-2026-10-09.md).

`audit_spark_mixed_funding_yields.py` consumes the exact normalized history hashed in the existing capital-readiness artifact. It compares daily Sky plus Savings principal to that independent original-capital control. Differences and uncertainty days are retained explicitly. It reuses settled revenue, yield denominators, daily rates and verified idle/SDE deductions.

The output contains **conditional Sky borrowing costs and conditional net APYs**, plus gross APYs. It always declares `publishable: false` and is not a monthly API publication artifact. The conditional net figure excludes Savings VSR financing expense and prime-level financing adjustments; it must not be described as all-in profitability.

## First run

The initial August 2026 run covers S1, S32, S3, S4, S23, S28, S5, S61, S34 and S62. It reuses the saved adapted history with audited Savings cash/interest operations. No new RPC extraction or historical revenue regeneration is needed.

```sh
PYTHONPATH=src:scripts .venv/bin/python scripts/audit_spark_mixed_funding_yields.py \
  --history /tmp/spark-adapted-origin-history.jsonl.gz \
  --readiness reconciliation/allocation_capital_readiness_2026_08.json.gz \
  --control settlements/spark/2026-08/provenance.json \
  --idle /tmp/pr215-resumed-spark/idle_deductions.json \
  --venues S1 S32 S3 S4 S23 S28 S5 S61 S34 S62 \
  --output /tmp/spark-mixed-funding-yields-2026-08.json
```

The source snapshot is intentionally unchanged. The replay traverses its historical transactions to carry the funding mix into August, rather than applying an August-end percentage to every day. The original readiness snapshot still supplies the capital control.

## Review and acceptance

Tests cover daily refinancing, immutable snapshots, exclusion of gains and VSR financing expenses, identical legacy replay outputs when snapshots are requested, and explicit capital gaps. They also assert that conditional results are not publishable.

The next review must inspect the actual daily differences and uncertain routes, resolve them against retained receipt evidence, and only then propose a measured split for normal monthly API use. A balanced transaction or a high month-end origin percentage alone is insufficient. Unsupported Savings scope and untraced receipts remain unknown.
