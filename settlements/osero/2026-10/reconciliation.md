# Osero reconciliation — October 2026 earning cycle

Reimburse **5,514 USDS** to the Osero subproxy through the October earning
cycle, normally settled in November. This is a settlement instruction,
not a claim that reimbursement has executed.

The July–September settlements minted the full borrowing cost as Osero
allocator debt. They also withheld the negative supply-side share from
Osero's Agent Rate and Distribution Rewards payment. That second deduction
charged the same operating shortfall again.

| Earning month | Full CoF debt minted | Executed payment | Corrected payment | Send-only reimbursement |
|---|---:|---:|---:|---:|
| July 2026 | 497 | 12,043 | 12,149 | 106 |
| August 2026 | 7,006 | 30,156 | 31,604 | 1,448 |
| September 2026 | 76,824 | 27,661 | 31,621 | 3,960 |
| **Total (USDS)** | **84,327** | **69,860** | **75,374** | **5,514** |

Each corrected payment is the historical workbook's full-precision AR + DR,
rounded once to whole USDS using the settlement convention (half up).
The refund is the difference from the actual executed payment. Summing the
unrounded supply deficits would give approximately 5,514.14 USDS; that is
not the cash withheld after transaction rounding.

Executed settlement transactions:

- July: [August 17 transaction](https://etherscan.io/tx/0x4d4f4d480a53ec6af365068283b90c59cf722aac6654920166c38e4f5c637bf4).
- August: [September 13 transaction](https://etherscan.io/tx/0xcd57535f7a9e60e61d34b31f793501fa6178b4ae37fe0b2900f76efd54e192c3).
- September: [October 12 transaction](https://etherscan.io/tx/0x93389082b75a9e278b6b2a2d7dde53618c38214b5d71699fd60461dbe1e10275).

The receipt excerpts confirm both the Osero debt grab and USDS payment to
`0x24fdcd3bFA5C2553e05B2f9AD0365EBC296278D3`. The historical workbook hashes,
receipt logs and settlement-block ilk rates are preserved in
`reconciliation/2026-10/osero_shortfall_evidence.json`. Reproduce the bridge:

```sh
python scripts/reconcile_osero_shortfalls.py
```

The existing `msc_preview['2026-10']['osero'].send_credit: '5514'` adds the
refund to Osero's October payment without increasing debt minted. It reduces
Sky's MSC net revenue by 5,514 USDS. No `sky_adj`, `sv_adj` or rate change is
needed. No earlier Osero reconciliation credit was identified in the reviewed
configuration or the subproxy receipts.

From the October earning cycle onward, allocator borrowers' current negative
supply share remains visible as an economic loss but no longer reduces the
subproxy payment: the full Sky borrowing cost is already debt-financed.
Explicit prior-cycle adjustments remain signed. Earlier reports and published
mint/send instructions are preserved; this report corrects their cashflows
through a separate refund. Final October transfer amounts also depend on the
ordinary October results.
