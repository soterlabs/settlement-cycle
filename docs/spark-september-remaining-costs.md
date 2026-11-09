# Spark September: refined residual after identifying RLUSD deposits

**Superseded PSM treatment (2026-11-09):** PSM is not an allocation and has no
allocation borrowing cost. Any PSM rate-equivalent amounts below are historical
reconciliation diagnostics, not venue charges. See the
[current correction](spark-explicit-custody-cost-rows.md).

The original net residual was $401,755.11. Transactions now tied to Sentora and
spRLUSD account for $363,345.66 of its old replay costs. This note decomposes the
remaining **$38,409.45** using saved snapshots. It does not claim that the two
positions have already been integrated into a corrected funding replay.

| Component | September cost contribution |
|---|---:|
| PSM custody: gross borrowed cost less global USDS/USDC deductions | +$119,401.10 |
| Savings-interest financing accounts | +$50,430.05 |
| Remaining synthetic unmatched outflow accounts | +$6,462.06 |
| CCTP transit accounts | +$510.50 |
| Selected pending/entrypoint accounts without a pricing policy | +$69.46 |
| Rounding accounts | +$0.01 |
| Raw-USDS global deduction beyond its Sky-funded principal | −$94,593.70 |
| S62 deduction beyond its Sky-funded principal | −$43,870.01 |
| S24/S25 deduction floors | less than $0.01 |
| Missing independent cash debt | $0.00 |
| **Net, before rounding components** | **$38,409.45** |

## PSM custody is a known position, not an unidentified destination

The existing capital normalizer retains non-transferable PSM shares in `psm:`
accounts. The selected conditional venue rows omit those accounts. Their gross
cost is $184,088.20. The existing global methodology deducts both:

- idle USDS: $27,868.36 of cost equivalent;
- the PSM USDC SDE leg: $36,818.74 of cost equivalent.

This yields $119,401.10 as a **grouped bridge component**. It is not yet a
certified per-chain PSM cost allocation: apply each PSM's funded basis and
leg-specific deductions when emitting the final rows. The sUSDS leg remains in
the base-rate calculation, with the existing spread credit handled separately.

The USDC deduction is recovered independently as daily `sde_av` minus the
explicit venue SDE daily balances, matching `compute/sky_revenue.py`. The earlier
$80,688.76 aggregate labelled LP/SDE difference included this PSM exemption;
only $43,870.01 is the material venue-level floor effect.

## Global deductions use a different base from per-allocation costs

Raw-USDS global deductions total $281,139.49; the selected cash accounts carry
only $186,545.79 of Sky-funded cost equivalent. The $94,593.70 difference is:

- $43,352.03 attributable to Savings principal in those cash accounts;
- $51,241.67 attributable to observed value above both tracked funding sources.

The latter is not automatically earned profit: the saved ledger also retains
uncertainty. It must not be promoted to borrowed principal simply to close the
comparison. Nearly all of this difference is in the Arbitrum ALM cash account;
the Base account contributes about $84.95.

S62 is the USDT/USDS LP. Both its USDT SDE leg and idle-USDS leg are deductible.
The global calculation deducts $153,025.47 of cost equivalent, whereas the
conditional funded principal supports only $109,155.46. Per-venue costs floor
at zero on all 30 days, leaving a $43,870.01 global deduction not represented by
a negative allocation cost. This does not demonstrate missing capital or a new
interest-rate error. Do not remove the global deduction or invent capital to
make the totals match.

## Smaller unmatched accounts

After removing the verified RLUSD deposit transactions, remaining unmatched
outflow costs are $6,462.06. Only **$31.00** comes from September-created
accounts; $6,431.05 comes from earlier movements carrying basis into September.
These are September costs, not proposed restatements of earlier expenses.

The largest individual account contributes only about **$36.77**. The leading
examples are July 17, 2025 swaps from 20M USDC into about 19.982M USDe. The
normalizer leaves the difference in a synthetic unallocated account. This is an
execution-price/fee/loss candidate, not evidence of a missing 20M allocation;
verify the exact execution before assigning it as an expense or changing basis.
The two largest outflow-shape groups are USDe ($2,374.76) and USDC ($2,145.67).

The ranked transaction evidence is in
`reconciliation/spark_remaining_outflows_2026_09.json`. It retains transaction
identities, original normalized movements and the September cost associated
with each sampled residual account.

## Next implementation order

1. Integrate the verified Sentora/S70 receipt movements under the newly proposed
   MSC config (#267), including the five withdrawals and the treasury receipt.
2. Emit explicit PSM custody rows with existing per-leg deductions. No further
   origin tracing is required to identify these accounts.
3. Keep global deduction credits visible in the reconciliation bridge when they
   exceed the corresponding account's Sky basis. Any redistribution across
   other venues is a new attribution rule, not an automatic arithmetic fix.
4. Review retained Savings-interest financing basis separately from live
   investment capital. The ledger's proportional funding convention is not
   proof that new Sky debt directly paid VSR interest.
5. Resolve the small historical conversion residuals, CCTP transit and pending
   custody mappings. Avoid another whole-history provider collection.

## Reproduction

```sh
python scripts/audit_spark_remaining_gap.py \
  --saved-dir /tmp/spark-september-financing-results \
  --control /tmp/september-allocation-inputs/spark/provenance.json \
  --idle reconciliation/spark_idle_deductions_2026_09.json \
  --routes reconciliation/spark_rlusd_routes_2026_09.json \
  --output reconciliation/spark_remaining_gap_2026_09.json
```

The diagnostic verifies complete daily funding stocks, independently checks raw
USDS observed value against the global deduction and checks exact closure of the
refined components. All figures retain Decimal precision. The output is
research-only and includes input hashes. No published settlement, API record,
rate, principal classification or allocation cost was modified by this analysis.
