# August 2026 allocation cost reconciliation excluding MSC financing

Validated 2026-09-22 for PR #215. Operator instruction: exclude borrowing
costs on MSC-created debt from the allocation comparison. Do not change the
actual global borrowing charge, allocation costs, or published settlement.

## Method

Read historical Vat `frob` and `grab` events for every configured ilk through
the published August end block. Nonzero `grab` events in these three histories
are positive and name the Vow (`0xa950524441892a31ebddf91d3ceefa04bf454466`)
as their debt destination. They represent the MSC debt component, separately
from cash draws used to fund allocations. Zero-dart initialization events are
excluded. The August event is on August 17, block 25775271.

For each day, scale cumulative normalized debt by that ilk's end-of-day Vat
rate. Independently summing `frob + grab` reproduces the published daily global
debt to within one cent for all three primes. Use the existing daily effective
borrowing rate (`daily_sky_rev / utilized` when utilization is positive).
MSC-related borrowing cost is outstanding MSC debt times that rate, capped
at the global charged utilization so a zero-utilization day cannot create a
negative adjusted charge. Separate debt present before August from debt added
during August. Earlier MSC debt continues to incur costs in August.

This is an analytical exclusion from the existing charge, using its existing
effective rate, not a recalculation of the subsidy or settlement. Grove's
comparison is combined across its two ilks; this does not establish per-ilk
allocation reconciliation. Event debt and rate scaling were checked separately
for both ilks.

## Results (USD)

| Prime | Cost on prior MSC debt | Cost on August MSC debt | Total excluded |
|---|---:|---:|---:|
| OBEX | 49,083.077770 | 3,819.017474 | 52,902.095244 |
| Osero | 0 | 0.748453 | 0.748453 |
| Grove | 245,332.407077 | 14,545.709950 | 259,878.117027 |

The August 17 debt additions are $2,535,968 for OBEX, $497 for Osero, and
$9,685,438 for Grove. Excluding only costs on these new additions would leave
the cost of earlier MSC debt in the comparison.

| Prime | Existing global cost | Global less MSC cost | Allocation cost | Allocation minus adjusted global |
|---|---:|---:|---:|---:|
| OBEX | 1,248,716.853282 | 1,195,814.758038 | 1,195,814.756792 | -0.001246 |
| Osero | 7,005.670169 | 7,004.921716 | 7,004.949568 | +0.027851 |
| Grove | 3,720,604.844326 | 3,460,726.727300 | 6,222.614398 (partial) | -3,454,504.112901 (incomplete) |

OBEX passes the adjusted one-cent comparison and has complete source coverage.
Osero still fails the one-cent comparison. Its remaining difference is cash
basis/deduction treatment, not the MSC debt component; lending-idle inputs
match. It also retains one unmatched receipt ($1.078572), so source coverage
is not complete. Do not silently round the residual away or change tolerance.

Grove's replay completed, but 24 allocations have unresolved funding, with
559 unmatched receipts, 528 unmatched outflows, and two unsupported positions
(E21 off-chain principal matching and E36 custody). Its cost subtotal is not
an exhaustive allocation cost. The MSC exclusion cannot repair these gaps.

## Validation evidence

Published controls: `settlements/{obex,osero,grove}/2026-08/provenance.json`.
Local detailed calculations: `/tmp/msc-adjusted-reconciliation.json`, with
per-day/per-ilk debt and Vat rates, cost splits, and underlying MSC events.
The read-only calculation is `/tmp/check_msc_adjusted_reconciliation.py`,
using `/tmp/msc-debt-events.json` and the completed allocation validation
outputs. Those temporary files are local evidence, not committed fixtures.
OBEX and Osero results independently reproduce the earlier decomposition.

These adjusted comparisons supplement the raw global comparison in the
existing validation output; no production computation or published artifact
was changed. Spark's financial validation was still running when this note
was written.
