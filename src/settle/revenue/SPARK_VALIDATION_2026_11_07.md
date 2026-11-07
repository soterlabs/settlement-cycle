# Spark reporting validation: January–October 2026

Checked 7 November 2026 against the live reporting API and production dashboard. API as-of date: 6 November 2026. Amounts below are whole USD, with no principal included in income. These are known amounts from incomplete reports, not certified complete balances or accuracy scores.

## End-to-end publication checks

- Both annual API responses pass the dashboard’s reporting contracts. All ten months select month-end income and cash-flow publications.
- The production income CSV matches API-derived January–October cells and comparable YTD totals for all 121 displayed/exported rows, including nested rows. Cash-flow and reconciliation CSV downloads also succeed.
- Dashboard-composed net income agrees with cash-flow net income for every month within 1e-18 USD. Provisional workbook expenses are included once, and pending contributor compensation remains explicitly missing.
- November MTD remains visible. Comparable Spark YTD totals stop at October, the last supplied expense month.
- No Next.js or Chromium server was started for these checks; validation used HTTP downloads and the existing pure dashboard readers.

## Monthly cash reconciliation

`Gap = change in measured cash − net cash change per statement`. Percentage is `abs(gap) / measured closing cash × 100`. Every month remains incomplete; a small percentage does not certify missing chain coverage, complete classification or correct recognition.

| Month | Measured closing cash, known | Gap, known | Absolute gap / closing cash | Unclassified events | Status |
|---|---:|---:|---:|---:|---|
| 2026-01 | 415,022,462 | 239,657 | 0.06% | 12,493 | Incomplete |
| 2026-02 | 422,218,829 | -404,596 | 0.10% | 8,578 | Incomplete |
| 2026-03 | 420,242,233 | -1,417,061 | 0.34% | 13,283 | Incomplete |
| 2026-04 | 900,491,616 | 779,503 | 0.09% | 9,632 | Incomplete |
| 2026-05 | 1,024,211,065 | 663,636 | 0.06% | 6,117 | Incomplete |
| 2026-06 | 708,783,809 | 934,699 | 0.13% | 5,834 | Incomplete |
| 2026-07 | 185,943,411 | 7,763,341 | 4.18% | 4,703 | Incomplete |
| 2026-08 | 191,999,386 | -3,682,690 | 1.92% | 14,142 | Incomplete |
| 2026-09 | 224,409,267 | 286,503 | 0.13% | 8,880 | Incomplete |
| 2026-10 | 190,058,890 | 1,652,778 | 0.87% | 14,383 | Incomplete |

## Where the numerical remainder sits

Separate the event/balance check from the indirect accounting bridge before changing classification. Let **D** be the sum of the saved classified cash-event leaves: Sky receipts, external receipts, expenses paid, Savings interest paid, SDE deployments, treasury disposals, allocation changes, investments, borrowings, deposits, contributions and buybacks.

- **Event/balance remainder = measured cash change − D.** This still includes omitted/unclassified movements and capture/routing gaps; it is not automatically an accounting expense.
- **Indirect adjustment = statement cash change − D.** This contains net income, noncash reversals, approved earned-not-received amounts and booked expense adjustments.
- **Gap = event/balance remainder − indirect adjustment.** These are overlapping diagnostic controls, not independent error estimates or proposed plug entries.

| Month | Event/balance remainder | Indirect adjustment | Gap |
|---|---:|---:|---:|
| 2026-01 | 662,584 | 422,927 | 239,657 |
| 2026-02 | 833,611 | 1,238,207 | -404,596 |
| 2026-03 | -452,805 | 964,256 | -1,417,061 |
| 2026-04 | 612,208 | -167,295 | 779,503 |
| 2026-05 | 53,877 | -609,759 | 663,636 |
| 2026-06 | 504,169 | -430,530 | 934,699 |
| 2026-07 | 1,282,576 | -6,480,765 | 7,763,341 |
| 2026-08 | -4,201,751 | -519,060 | -3,682,690 |
| 2026-09 | -280,350 | -566,853 | 286,503 |
| 2026-10 | 552,626 | -1,100,151 | 1,652,778 |

## Priorities and remaining gaps

| Priority | Finding | Evidence / size | Next action |
|---|---|---|---|
| 1 | September accounting refresh failed before collection | Older revenue row omits Robinhood; retained reporting opening/closing pins are 51,274,667 / 76,928,650. September source lacks treasury and ownership objects. The original cash replay succeeded using that older source. | Preserve certified reporting-only chain pins, reject actual shared-chain disagreements, then publish corrected September source and replay its cash flow. Resolution is recorded below. |
| 2 | July indirect bridge | Gap 7,763,341; event/balance remainder 1,282,576; indirect adjustment -6,480,765. The approved MSC entitlement reversal is -9,567,225, while other noncash/Savings adjustments are 15,515,461 and nonstable gain reversal is -13,356,727. | Reconcile earned MSC components to each income/receivable line and settlement timing; verify each Savings add-back offsets the expense once. Do not force a residual to income or expense. |
| 3 | August event/balance bridge | Gap -3,682,690; event/balance remainder -4,201,751. Two unmatched Savings payments total -0.244430 in the ledger. | Pair routing/bridge legs and transaction cash boundaries first. The small Savings exceptions cannot explain the multi-million remainder. |
| 4 | October | Gap 1,652,778; event/balance remainder 552,626; indirect adjustment -1,100,151. DR and a regular MSC entitlement are not yet available; a known prior-month true-up is not a full October entitlement. | Ingest approved October DR and entitlement when published, and refresh both income and cash flow together. |
| 5 | September unresolved counterparty | One acknowledged unresolved cash payment of -1,060,000, plus a 0.01 unmatched Savings payment. | Establish counterparty/purpose from evidence before classifying. Neither amount is assumed to equal the net reconciliation gap. |
| 6 | Missing coverage | xlayer, bsc and gnosis are listed as unavailable; Robinhood USDC lacks a reviewed token entry. Allocation basis exceptions include S14/S15/S32 incoming share costs and several vault asset getter mismatches. | Verify deployment dates/token identity and recover acquisition costs or reviewed contract adapters. Missing balances cannot be assigned a credible USD error bound yet. |
| 7 | Unclassified/routing movements | Saved unclassified net totals are much larger than the reconciliation gaps and may offset excluded internal/routing movements. | Match at transaction/message level and retain both legs. Do not add the full unclassified net to the gap or describe it as the size of the unknown error. |

The remaining *unknown* error size cannot be measured from these inputs: missing chains, balances, receipt purposes and cost bases have no supported upper bound. The reported gap is the known arithmetic residual, not a bound on all possible errors.

## Pending source inputs

| Input | Checked source | Current treatment |
|---|---|---|
| October Distribution Rewards | `soterlabs/settlement-reports/reports/spark` and `settlement-cycle/data/distribution_rewards` only have published months through September at this check; October API DR is unavailable. | Keep TBD. Import and replay once the approved October source is published; no estimate substituted. |
| Contributor SPK compensation | `contributor_compensation.json` has no approved monthly amounts. The local workbook SHA-256 matches the already imported expense source (`e6a3260653dceca893645fd03fd49b254ed98f4ec41a2f402972fa7dc5f656f6`). | Keep the separate Compensation and benefits component pending; do not infer expense from vesting transfers or add it again to existing token incentives. |

## September repair result

Pending the reviewed refresh and replay.

## Snapshot references

- Income report hash: `3245d2d597a870322829ffc97d3eadfeeec5533e62a822b785b7e5a61de645de`.
- Cash-flow report hash: `8a4048644634d651b0afbcc6ce47dcba2e3ab5014348f0d5746703229fbd6ab8`.
- Endpoint: `/v3/reporting/statements?prime=spark&year=2026&accounting=true`.
- Endpoint: `/v3/reporting/cash-flow?prime=spark&year=2026`.
- API monthly sources and cash revisions are immutable; live selections can advance. Numbers above describe this dated snapshot and must be refreshed after the September replay.
- Accounting rules: [ACCOUNTING_RULES.md](ACCOUNTING_RULES.md).
