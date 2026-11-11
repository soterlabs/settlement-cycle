# Deferred Spark funding refinements

Decision: operator, 2026-11-11, accepted acknowledging the $26,594.30 September
PSM3 funding-value cost equivalent and proceeding with existing per-venue
borrowing-cost estimates. Refining its funding attribution is follow-up work,
not a prerequisite for producing those estimates.

This is not $26,594 of unidentified capital, an approved extra charge, or a
PSM allocation. It is a rate-weighted value difference. Do not spread it across
venues, zero it out, or silently amend the global settlement amount.

## Current deliverable

`reconciliation/spark_september_venue_borrowing_costs.json` and `.csv` contain
58 calculated venue rows, including the corrected S70 and S72 RLUSD positions,
using the existing proportional Savings-funding conventions. Calculated zero
costs remain zero; absence of calculation remains null.

The ten configured rows outside this replay's investment-cost calculation are:
S56/S57/S58/S59/S60/S63 (Savings products/liabilities), S67/S68/S69 (incentive
receivables), and S71 (reporting-only spUSDG, excluded from CoF in the captured
configuration). This export does not invent investment capital or change those
rows' accounting scope. S70's newly calculated cost is distinct from its revenue
coverage change merged in PR267.

Venue cost subtotal: $7,538,457.84. Separate deduction credits: -$138,463.70.
Subtotal including those credits: $7,399,994.14. Credits are kept separate from
nonnegative venue costs, not redistributed as a residual adjustment.

The completed replay and custody analysis inputs are saved at
`/tmp/spark-september-rlusd-replay`; the export records their hashes. Generate it
using `scripts/export_spark_venue_borrowing_costs.py`. No full replay is needed
for this export. The reviewed publication path is `scripts/prepare_spark_monthly_cost_publication.py`,
which writes monthly allocation analytics only, from September 2026 onward. It
reuses the pinned revenue control; S70/S72 revenue absent from that control stays
null. It does not restate settlements or recalculate revenue. Publication checks
evidence hashes, each venue cost, period alignment, and the existing API revision.

The bridge evidence predates PR274, now included by this rebase. Native idle
USDS in bridge transit is exempt under that merged policy; historical bridge
comparison figures are retained as evidence, not new allocation charges. CCTP
USDC transit remains subject to the existing borrowing-cost treatment.

## Follow-up scope

1. Explain the remaining PSM3 full-value-minus-funded-principal difference.
   Existing evidence: `docs/spark-september-psm3-comparison.md`,
   `reconciliation/spark_psm3_2026_09/`, and the corrected
   `custody-analysis.json` in the replay directory. Of the previous $57,241.41
   comparison, $30,647.10 is identified Savings principal and $26,594.30 remains
   unclassified. Do not presume that all remaining value is earned income.
2. Review the $50,430.05 saver-interest financing attribution separately.
   Deferral of the PSM3 question does not constitute a new financing policy.
3. Refine ~$6.5k of smaller exchange/entry-point differences. Bridge custody
   checks are in `reconciliation/spark_bridge_controls_2026_09/`.
4. Preserve MSC debt financing and historical Vat accrual as explicit separate
   items. Sum venue costs, separate credits, financing, and remaining comparison
   items against the unchanged $8,226,734.61 global borrowing cost.

The $126,444.02 comparison after the existing financing rows is not erased by
this decision. Acknowledging an attribution limitation allows useful estimates;
it does not certify that the gross venue subtotal equals the global charge.
