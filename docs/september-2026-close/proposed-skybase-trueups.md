# Approved Skybase historical payment true-ups

Operator approved these exact adjustments on October 1, 2026. The four items
are configured in `config/settlement_adjustments.yaml`, paid with September,
and shown separately from normal September accrual in the summary and workbook.
They do not enter September-earned DR or accrual-basis Sky Net Revenue / TMF.
Published January–August reports are not regenerated.

| Item | Earned period | USDS |
|---|---|---:|
| Pendle / code 1997 | January–August 2026 | 27,740.235315 |
| Flagship / code 1998 | January–August 2026 | 34,229.172646 |
| Risk Capital / code 1999 | January–August 2026 | 758.752668 |
| Grove Farm / codes 0/1/1002 | July–August 2026 | 61,966.169912 |
| **Total** | | **124,694.330541** |

Evidence: `settle-dr-dune@1e9ecb2/docs/september-2026-settlement.md`, which audits
published reports at `soterlabs/settlement-reports@cb3db5ce974f22361cff8f2a0aef1bde26aa05d7`.
The exact amounts above are the operator's approved payment instructions.
They are not recomputed from the historical sheet during report generation.
The writer replaces the payment bridge on every run, preventing duplicate additions.

Normal September accrual comes independently from the finalized workbook and
its full-precision companion CSV, snapshotted under
`data/distribution_rewards/2026-09/`. Hashes bind both sources; the CSV aggregates
must match the workbook to its half-cent rounding precision. Only September
rows are imported. The historical additions sheet is never imported as accrual.
Grove Farm retains its emitted codes: 1/1002 to Skybase, 2009 to Grove, and
-999999 unpaid. Code 1020 remains 1inch / Skybase; 99/10000/10001 remain unpaid.

Codes 123/232/234/3003/3123 have no confirmed owner and remain explicitly
withheld under `unattributed.pending_ownership`. This is not an ownership
assignment. Their full-precision amounts are retained in the input and close
audit so they can be resolved later without disappearing from the accounting.
