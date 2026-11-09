# Explicit PSM costs and deduction credits

Implemented in the saved funding-cost calculation, without modifying global
borrowing costs, debt or settlement amounts. This is conditional analytics,
not an API publication. The reusable calculation primitives live in
`src/settle/compute/allocation_cost_rows.py`; the saved September builder is
`scripts/build_spark_cost_rows.py`.

## PSM rows

Each `psm:<chain>:<contract>:<holder>` account now produces a cost row for each
funding ilk. Only its saved Sky debt basis is priced; position NAV, Savings
principal and earnings are not substituted for borrowed capital. Rows retain
daily principal and daily cost, with chain, contract, holder and ilk identifiers.
Accounts already in selected allocation costs are rejected to prevent duplication.

September 2026 gross costs:

| Chain | Borrowing cost before shared deductions |
|---|---:|
| Arbitrum | $169,281.16 |
| Base | $10,219.45 |
| Optimism | $4,485.38 |
| Unichain | $102.21 |
| **Total** | **$184,088.20** |

The independently calculated global PSM credits remain explicit separate rows:
idle USDS **−$27,868.36**, USDC SDE **−$36,818.74**. Combined PSM cost after those
credits is **$119,401.10**. The saved controls do not identify per-chain leg
ownership, so per-chain net costs remain unavailable. The deductions are not
prorated over chains or duplicated in each chain row. The existing sUSDS spread
reimbursement remains outside this base-rate cost comparison.

## Excess credits

Existing venue costs retain their zero floor. For principal P, deductible value
D and effective daily rate r:

- venue cost remains `max(P − D, 0) × r`;
- the separate excess credit is `−max(D − P, 0) × r`.

Together these reproduce `(P − D) × r` without negative principal, negative
venue yield denominators, or a second deduction of the portion already applied.
Zero-funded positions still retain their genuine global credit.

For raw idle USDS, the existing calculation excludes the whole Sky-funded basis.
Its adjustment is `(Sky basis − observed idle value) × r`. This is deliberately
signed: if basis exceeds value, it reverses the excess exclusion instead of
silently dropping it. Each daily observed total must match the independent
ALM-USDS global control.

September adjustments are **−$94,593.70** for raw USDS (mostly Arbitrum), and
**−$43,870.01** for S62. S24/S25 add less than one cent. Credits remain attached
to their source account/venue or the PSM pool; they are not redistributed to
unrelated allocations or subtracted from revenue.

## September reconciliation

| Item | Cost |
|---|---:|
| Previously priced conditional allocations | $7,175,113.09 |
| Added PSM gross rows | +$184,088.20 |
| Shared PSM credits | −$64,687.10 |
| Raw-USDS and venue excess credits | −$138,463.71 |
| **Allocations/custody including credits** | **$7,156,050.48** |
| Separate MSC debt financing | +$418,453.22 |
| Separate historical Vat-accrual financing | +$231,413.18 |
| Retained Savings-interest financing basis | +$50,430.05 |
| **Still unassigned in the old replay** | **+$370,387.67** |
| **Independent global borrowing cost** | **$8,226,734.61** |

Of the unassigned amount, **$363,345.66** is associated with the verified RLUSD
deposit routes but awaits corrected receipt-position replay. After that identified
amount, **$7,042.01** remains: unmatched outflows $6,462.06, CCTP $510.50,
unpriced custody $69.46 and rounding. Rounding individual table rows can differ
from full-precision totals by a cent.

The $38,409.45 previous net residual has not been erased by a plug: PSM custody,
credits and Savings-interest basis have moved into explicitly named rows. The
Savings-interest source attribution remains conditional; naming it does not
prove that new Sky borrowing paid VSR interest. Likewise, identified RLUSD
costs remain outside the validated allocation subtotal until the replay changes.

## Reproduce

```sh
PYTHONPATH=src python scripts/build_spark_cost_rows.py \
  --saved-dir /tmp/spark-september-financing-results \
  --control /tmp/september-allocation-inputs/spark/provenance.json \
  --idle reconciliation/spark_idle_deductions_2026_09.json \
  --routes reconciliation/spark_rlusd_routes_2026_09.json \
  --output reconciliation/spark_explicit_cost_rows_2026_09.json
```

This loads saved snapshots only, checks daily stock controls and cost closure,
and records SHA-256 hashes of all inputs. It does not collect or replay historical
events, modify existing inputs, regenerate settlements or publish API data.
Tests cover multiple chains/ilks, duplicate PSM coverage, partial and excess
deductions, zero funding, malformed amounts and missing daily data.
