# Retain sub-cent repayment differences without calling them missing capital

The February 19 aToken withdrawal/debt repayment has a **$0.000000505865**
difference between the reconstructed scaled-balance debit and the exact
underlying cash repaid. Incoming asset legs already apply a one-cent threshold
for identifying unmatched funding, but repayments unconditionally added even
these tiny differences to `unmatched_receipts`.

Repayments now use the same existing threshold. The full numerical difference
remains in the ledger and in a separate `rounding_receipts` diagnostic; no debt,
repayment, rate or borrowing-cost value is changed. Larger differences still
produce unmatched receipts and propagate uncertainty. Tests cover both sides
and check that the exact debt repayment and realized principal difference remain.

Grove's August replay has **3 material receipt gaps**, plus the separately
recorded sub-microdollar rounding item. The remaining receipts are **5 RLUSD**,
**49,591 RLUSD**, and **1 USDC**. Unmatched outflows remain 239; eligible costs
remain $11,784.59. This display correction does not reconcile BLOOM-A.

Evidence: `reconciliation/grove_repayment_rounding_2026_08.json`.
