# Allocation deduction credits; PSM remains outside allocations

Operator clarification, 2026-11-09: **PSM is not an allocation venue and must not
have an allocation borrowing-cost or yield row.** This supersedes the previous
PSM pricing proposal in this branch.

`allocation_cost_rows.py` now retains PSM principal only as a custody inventory,
with `is_allocation: false`. The inventory has no rate, borrowing-cost or yield
fields. PSM is excluded from `additional_cost_rows` and from allocation cost
subtotals. A PSM account already selected as an allocation is rejected.

The existing global PSM treatment is unchanged. Saved global USDS/USDC
deductions and the old replay's PSM-basis rate equivalent remain diagnostic
controls outside allocation costs. The resulting $119,401.10 comparison
difference is **not an allocation charge or a resolved allocation attribution**.
No amount is redistributed to another venue to hide that difference.

## Supported allocation credits

Existing venue costs retain their zero floor. For principal P, deductible value
D and effective daily rate r, the venue cost is `max(P − D, 0) × r` and the
separate excess credit is `−max(D − P, 0) × r`. The sum reproduces the global
arithmetic without inventing negative principal or negative yield denominators.
The portion already deducted is not deducted a second time.

For raw idle USDS, the prior calculation excludes the whole Sky-funded basis.
The signed adjustment is `(Sky basis − observed idle value) × r`. Daily observed
idle value must match the independent global ALM-USDS control. Credits remain
attached to their source; they are not redistributed.

## September comparison after withdrawing PSM allocation pricing

- Existing conditional allocation costs: $7,175,113.09.
- Supported excess credits: −$138,463.71.
- Allocation subtotal after credits: $7,036,649.38.
- Separate MSC/Vat-accrual/Savings-interest financing remains explicit and
  conditional where applicable.
- Compared with unchanged global borrowing cost, $489,788.77 remains unassigned
  in the old replay. $363,345.66 is tied to identified RLUSD routes pending
  corrected replay, leaving **$126,443.12** beyond those routes.

That last amount includes the $119,401.10 global-versus-allocation comparison
item and $7,042.01 of smaller residuals. The earlier claim that only $7,042.01
remained relied on assigning PSM an allocation cost and is withdrawn. These
are diagnostic differences, not a change to Spark's charge or settlement.

```sh
PYTHONPATH=src python scripts/build_spark_cost_rows.py \
  --saved-dir /tmp/spark-september-financing-results \
  --control /tmp/september-allocation-inputs/spark/provenance.json \
  --idle reconciliation/spark_idle_deductions_2026_09.json \
  --routes reconciliation/spark_rlusd_routes_2026_09.json \
  --output reconciliation/spark_explicit_cost_rows_2026_09.json
```

The builder reads saved evidence, verifies controls, and writes a research-only
artifact. It does not regenerate settlement reports or publish API data. Tests
verify that PSM inventory has no cost/yield fields and is not an allocation,
alongside deduction conservation, invalid amounts and missing daily controls.
