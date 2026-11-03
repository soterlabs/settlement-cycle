# August Spark borrowing costs: remaining readiness checks

November 3, 2026. This review uses saved daily balances and controls; no
historical replay, RPC extraction, database change or API publication occurred.

## S66 financing now calculable independently of revenue

The S66 configuration explicitly describes a non-SDE RLUSD/USDS pool whose
USDS leg is deductible. Saved idle evidence covers all 31 August days and
reconciles to the settlement's aggregate idle control. Under the conditional
funding split its borrowing cost is **$2,545.24**. Its gross/net yields remain
unavailable because the revenue row is missing; no zero revenue is assumed.

The audit accepts an explicit period-scoped cost-policy file for such rows.
The policy requires complete daily idle deductions and does not override an
existing settlement row. Missing deductions leave the cost unavailable.
The reviewed August policy is
`reconciliation/spark_missing_revenue_cost_policy_2026_08.json`.

## All configured allocations represented in the saved capital data

The same saved Sky-principal model now supplies costs for **56 configured
allocations**, totaling **$5,530,034.10**. Savings liability rows and intermediate
custody rows are not counted as extra chargeable investment venues. This avoids
mixing the 28 mixed-funding results with older assumption-only principal values
for other allocations. No overlapping allocation account was counted twice.

| Bridge component | USD |
|---|---:|
| Conditional costs for 56 configured allocations | 5,530,034.10 |
| Financing of other captured accounts | 68.47 |
| Sky debt financing not represented in captured basis — **unclassified residual** | 231,391.92 |
| Independently reconstructed cumulative Vat-rate financing | 234,768.88 |
| Allocation deductions minus global deductions | -284,834.87 |
| Global borrowing costs excluding MSC | **5,711,428.50** |
| MSC-related financing | 397,481.84 |
| Published global borrowing costs | **6,108,910.34** |

The arithmetic closes below one cent. **This is not a completed funding
reconciliation**: the $231,391.92 term is calculated as the financing of debt
remaining after MSC, independently reconstructed Vat accrual, and the captured
Sky basis. It is not an independently classified expense or allocation.
The net allocation/global gap after MSC is $181,394.40; the table shows why that
is different from the unclassified component. Do not force either residual into
venue costs.

The independent daily Vat proof reproduces non-MSC debt in the saved debt
control. Daily rates come from the unchanged settlement. Deduction differences
are measured from the global utilized control and actual allocation-level
applied deductions, including their caps, rather than estimated as a balancing
adjustment.

## Savings stock comparison remains separate

Outstanding Savings principal exceeds the 140 captured account balances by
**$7,607,924.34**. This is a principal stock, not a borrowing expense, and cannot
be added to the USD cost table above. The saved snapshots omit the full ledger's
unallocated/financing accounts and lender-specific losses. They cannot identify
how that remainder is split among those categories.

Older complete-ledger artifacts use different normalized histories/policies.
Their previous zero residual cannot certify the current result. No new full
replay has been started to fill this evidence gap.

## Readiness decision

The S66 cost fix and conditional 56-allocation calculations are tested and
reviewable. PR #236 is **not yet ready to claim certified funding closure or
publish these splits**. The remaining evidence needed is a same-run aggregate
of excluded Sky/Savings financing accounts and losses, or an independently
supported classification of those amounts. The amounts stay visible and
unassigned until that evidence is available.

Artifacts:
- `reconciliation/spark_saved_global_cost_bridge_2026_08.json`
- `reconciliation/spark_all_configured_funding_costs_2026_08.json.gz`
- `reconciliation/spark_saved_funding_validation_2026_08.json.gz`

## Final PR reviews

Two sequential review/fix rounds added cross-venue account uniqueness checks
and strengthened the transaction diagnostic's reference check. The latter now
compares Sky principal, Savings principal, original capital and uncertainty
separately, so offsetting source-allocation errors cannot pass merely because
the combined gap is unchanged. Regression tests cover both failures.

The final targeted suite passes 82 tests. Saved August numbers were revalidated
without replay. These checks preserve the research-only scope and do not turn
the unclassified financing or Savings remainder into certified allocations.
