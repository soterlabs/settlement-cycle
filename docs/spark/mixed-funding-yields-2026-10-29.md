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

## Review fixes and coverage (October 29)

The first replay is still running on commit `4288cdc`; it has not validated a
new venue yet. Its ten selected allocations account for approximately 98.1% of
August average capital in the Savings-pending rows. This is workload coverage,
not an explained-funding percentage. There are 28 configured allocations and
six intermediate custody rows in that pending population.

After the first ten, prioritize S64 (idle RLUSD), S65 (BlueChip USDT Morpho),
S10 (BlueChip USDC Morpho), S2 (spUSDC) and S66 (RLUSD/USDS V4). Together these
account for about $54 million of average original capital. Then assess the
remaining 13 small or legacy allocations: S39, S45, S49, S53, S55, S27, S9,
S25, S11, S24, S54, S13 and S6. The six intermediate custody rows must carry
funding to its destination without becoming duplicate chargeable allocations.

Review found and fixed missing Anchorage exposure input: S23 must use audited
daily outstanding loan principal, not the escrow balance. The August evidence
is selected automatically only for August; other periods require their own
`--anchorage-exposure`. Missing evidence leaves the yield unavailable.

New runs persist compact funding snapshots before report generation. These
cover all measured allocation/custody accounts, allowing further venue reports
without repeating the historical replay. `--replay-snapshot` selects the cache;
its history, dates, accounts and replay-code hashes must match. Individual
negative/non-finite funding components are rejected even if their sum looks
valid. Evidence and code hashes are captured before execution and checked again
before writing results. Output writes are atomic.

The already-running replay predates these fixes and will not produce this
checkpoint. Preserve its result as research evidence and review its daily gaps;
do not label it as executing the revised code or publish its conditional APYs.
Its launch-code manifest is retained locally at
`/tmp/spark-mixed-funding-running-code-before-review.json`.
