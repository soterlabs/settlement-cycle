# Proposed Skybase prior-period adjustments

Status: accounting/payout treatment awaiting confirmation. These amounts are
not included in September accrual revenue, settlement totals, or TMF inputs.

Upstream DR PR #26 (`docs/september-2026-settlement.md`, commit `d878b59`)
records the following proposed additions to the September payment:

| Prior-period item | Period | USDS |
|---|---|---:|
| Pendle SY-sUSDS / PT-sUSDS backing, code 1997 | Jan–Aug 2026 | 27,740.24 |
| Morpho USDS Flagship, code 1998 | Jan–Aug 2026 | 34,229.17 |
| Morpho USDS Risk Capital, code 1999 | Jan–Aug 2026 | 758.75 |
| Grove-farm rewards on emitted codes 0/1/1002, payable to Skybase | Jul–Aug 2026 | 61,966.17 |
| **Total proposed payment adjustment** | | **124,694.33** |

The upstream audit compared published settlement reports at
`soterlabs/settlement-reports@cb3db5ce974f22361cff8f2a0aef1bde26aa05d7` and
found these amounts absent. That does not establish whether an unreported
payment was made outside those reports. Untagged Grove-farm rewards of
813.35 USDS are explicitly excluded from the proposed payment.

Proposed presentation, if confirmed: show this as a separate prior-period
payment adjustment alongside September accrual, without relabeling it as
September-earned revenue or regenerating prior settlement reports.
