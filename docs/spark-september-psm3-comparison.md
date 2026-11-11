# Spark September 2026 PSM3 comparison

Read-only audit, 2026-11-09. PSM3 is not an allocation venue. This audit does
not add a PSM borrowing-cost row or change settlement/API figures.

## Findings

The $119,401.10 diagnostic difference is not evidence of missed withdrawals.
For all four configured PSM3s, opening contract shares plus September deposit
shares minus withdrawal shares equals the closing contract shares exactly.
The independent holder-event histories contain 455 events on Arbitrum, 419
on Base, 22 on Optimism and 4 on Unichain (900 total). This checks the
aggregate share history, not every historical funding-source attribution.

Daily USDS and USDC reserve components independently reconstructed using the
existing PSM3 source reproduce the saved settlement deductions to substantially
less than a cent. No carry-forward warning occurred during this audit.

| September rate equivalent | Arbitrum | Base | Optimism | Unichain | Total |
|---|---:|---:|---:|---:|---:|
| Replay Sky-funded principal | 169,281.16 | 10,219.45 | 4,485.38 | 102.21 | 184,088.20 |
| Full PSM3 holding value | 224,731.07 | 10,972.05 | 5,494.81 | 131.68 | 241,329.61 |
| USDS deduction | 23,765.71 | 4,102.64 | 0.00 | 0.00 | 27,868.36 |
| USDC deduction | 30,596.72 | 3,057.48 | 3,048.79 | 115.75 | 36,818.74 |
| sUSDS component | 170,368.63 | 3,811.93 | 2,446.02 | 15.92 | 176,642.51 |
| Sky basis minus full holding | -55,449.91 | -752.61 | -1,009.43 | -29.46 | -57,241.41 |

These figures multiply daily values by the same effective daily rate used in
the saved global reconciliation. They are diagnostic rate equivalents, not
new PSM allocation charges. The identity is:

```
184,088.20 - 27,868.36 - 36,818.74 = 119,401.10
176,642.51 - 57,241.41             = 119,401.10
```

The existing global calculation does not deduct the sUSDS leg from utilized.
Separately, saved prime revenue includes $167,633.42 PSM3 sUSDS appreciation
and $9,486.73 PSM3 spread credit ($177,120.15 together). Those revenue credits
are not venue borrowing-cost deductions. They must not be subtracted from the
gross-cost reconciliation merely to force equality. Their $477.64 difference
from the effective-rate equivalent above has not been independently decomposed
here; the formulas use different rate/conversion conventions.

The full PSM3 holding exceeds the replay Sky-funded principal by an average
$18,696,506.11 during September, equivalent to $57,241.41 at the daily global
rate. That is the remaining funding-basis question: separate non-Sky principal,
earnings, and any attribution/valuation error. The share checks do not prove
that this whole difference is Savings-funded or earned. Do not classify it by
assumption or silently redistribute it to allocation venues.

Thus the earlier description of two disagreeing physical balances was too
strong: we were comparing borrowed principal with full position value and
omitting the sUSDS component from the explanation. The existing $119,401.10
comparison line is algebraically explained, but is not yet a fully validated
allocation of financing costs. The overall venue/global reconciliation numbers
are unchanged by this audit.

## Evidence and method

Evidence: `reconciliation/spark_psm3_2026_09/{arbitrum,base,optimism,unichain}.json`
contains daily leg values, opening and closing share controls and event-based
share histories. `comparison.json` contains daily Sky basis and rate equivalents.

- September pins and daily effective rates: saved
  `/tmp/september-allocation-inputs/spark/provenance.json`.
- Sky principal: saved `/tmp/spark-september-financing-results/daily-by-ilk.json.gz`.
  Their SHA256 hashes are already recorded in `spark_explicit_cost_rows_2026_09.json`.
- Daily legs: `get_psm_usds_timeseries`, with `HyperSyncPsm3Source` (opening RPC
  balances plus September events), HyperSync UTC end-of-day block resolution,
  and the existing mainnet sUSDS conversion source.
- Independent boundary controls: `rpc.psm3_shares` at both saved boundary pins.
- Deposits/withdrawals concern Spark's configured ALM shares, not all pool users.

No production code, published report or API record was changed.
