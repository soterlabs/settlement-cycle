# S66 August analytics repair

The finalized August settlement workbook and summary include S66 as non-SDE,
with revenue of -461.370572920806 USD. The older local provenance used by the
funding research omitted it. This repair supplies that existing revenue to
monthly allocation analytics; it does not restate settlement revenue.

The workbook's `Avg value` is 1,176,300.265828546 USD **after** the idle-USDS
deduction. The saved 31-day USDS series averages 2,622,246.761723101915645714548
USD. Adding it back reconstructs full-position average exposure of
3,798,547.027551647915645714548 USD. The non-SDE weight and explicit workbook
idle-deduction note are checked. The full exposure, rather than the reduced
borrowing-cost base, is used consistently for gross and net yield. This retains
Excel's published numeric precision; no greater original workbook precision is
claimed.

For each August day, the existing conditional Sky principal is reduced by the
measured USDS leg, floored at zero, and multiplied by the existing daily rate.
Savings principal is already excluded from Sky principal and is not deducted
again. There is no SDE exemption for RLUSD. This reproduces the previous
conditional cost exactly; it is not a change to the accepted funding/deduction
convention or a new certification of the funding split.

| Metric | August result |
|---|---:|
| Gross / prime-retained revenue | -$461.370572920806 |
| Conditional borrowing cost | $2,545.238240225381282622421558 |
| Conditional net PnL | -$3,006.608813146187282622421558 |
| Gross monthly return | -0.012146% |
| Conditional net monthly return | -0.079152% |
| Gross compounded annualized yield | -0.142916% |
| Conditional net compounded annualized yield | -0.927982% |

The workbook's $3,186.901270819541 CoF allocation uses the older proportional
allocation of total borrowing cost. It is retained as a comparison, not used
as the funding-based charge. None of these calculations changes the global
borrowing cost, published workbook, report summary or other allocations.

## Evidence and reproduction

`reconciliation/spark_s66_august_repair_manifest.json` pins the finalized
workbook, summary, conditional funding artifact, existing daily rate control and
S66-only idle-USDS series. All runtime evidence is committed. The latter is an
unchanged subset of the saved daily deduction control, whose original hash is
retained; no new RPC requests or full replay are required. The calculated result
including daily charges and hashes is in
`reconciliation/spark_s66_august_analytics.json`.

Save a complete current Spark August allocation API response as BASE.json, then:

```sh
PYTHONPATH=src .venv/bin/python scripts/repair_spark_s66_august.py \
  --base BASE.json --output REVIEWED.json
```

Dry-run is the default. Only S66's known revenue, full yield denominator, gross
yield and nested conditional net result are repaired. Its canonical borrowing
cost/net yield stay null and its funding uncertainty remains explicit. All other
allocation rows and the global conditional funding bridge are preserved.

After review, `--publish` creates a new immutable monthly allocation revision.
It verifies the stored baseline in a transaction, rejects a newer independent
revision and is idempotent on retry. Earlier revisions stay accessible. The
publisher refuses to overwrite input/evidence files. This PR does not execute
production publication, daily revenue regeneration or a historical settlement
restatement. September is outside this narrowly pinned correction.
