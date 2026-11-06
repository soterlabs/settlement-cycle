# Savings refinancing for allocation borrowing costs

The operator accepted proportional replacement of remaining Sky-funded principal
when Savings cash repays Sky debt without identifying a destination allocation.
This is an accounting convention, not proof of the historical origin of every
asset. It does not validate the separate provisional principal/VSR repayment
split or change global borrowing costs, settlement amounts or published API data.

Use normalized **net debt per transaction and per ilk**, never gross draws as
an additional source on top of the net amount. Preserve authenticated independent
routes (including the existing May 2 route split). Do not net unrelated
transactions or ilks together.

For a repayment, first remove the funding carried by the outgoing cash. For
replacement funding, reduce the repaid lender's remaining principal proportionally
and replace it with the funding carried by that cash. Gains carry no principal;
other ilks and Savings-only allocations cannot absorb a Sky principal reduction.
No funds are added to investment balances by refinancing. The existing
`allocation_external_funding.apply_external` implements this convention; this
change validates it against actual receipt amounts rather than duplicating it.

## Receipt witnesses

Three August 2026 spUSDC withdrawals exchange USDC 1:1 through the PSM and
repay Spark's ilk without depositing into sUSDS in the same transaction:

| Date | USDC | Transaction |
|---|---:|---|
| August 1 | 22,999,999.411302 | [receipt](https://etherscan.io/tx/0xada6c5d866c2403f2d40ca3c021692504cb054ac20b70b06091eda4617eb34b4) |
| August 1 | 2,999,999.401945 | [receipt](https://etherscan.io/tx/0xeb3daa6d87de86c7c15d1614518023b91c4211c7fd37676ca50155a03fd68986) |
| August 3 | 441,597.667349 | [receipt](https://etherscan.io/tx/0x18278647ea2ac65807135a07fb9fe7625975418f9005739bdc14ccfba4c21839) |

[August 27](https://etherscan.io/tx/0x10e4f12e3606c4a964d552cd1f79c40f67a0dfaae4e57d14b1a25fe45ded61dc)
withdraws 5M USDC, draws and deposits 4,999,995.035978691296114770 USDS into
sUSDS, and repays 5M USDS after the PSM exchange. The net change is approximately
-4.964021308704 USDS of Sky debt. Netting funds the new deposit with Savings
principal and proportionally replaces the remaining small Sky balance. The Vat
comparison uses Spark's actual non-unit rate; raw normalized debt is not USDS.

Full receipts are retained in
`tests/fixtures/spark_savings_net_repayments_2026_08.json.gz`. Tests use these
observed transaction amounts with an explicitly synthetic opening portfolio.
They do not claim to reconstruct the actual August portfolio or revised costs.

## Complete saved principal controls

Research replays requesting funding snapshots now also retain a daily
`daily_funding_bridge`. For Sky (all ilks combined) and each Savings source it
reports outstanding principal, selected-account principal, financing-account
principal, other-account principal, cumulative realised losses and the remaining
`principal_not_in_accounts` difference. Financing accounts are not investment
venues. Cumulative losses can later be refinanced, so they are shown separately,
not automatically deducted from today's difference.

Snapshot schema 3 requires these controls; old snapshots cannot silently pass as
complete controls. Reload checks their arithmetic and selected-account totals.
The ordinary borrowing-cost path does not collect this additional diagnostic.
The research report includes the controls for subsequent gap investigations.

This is a principal-stock bridge, not the global borrowing-cost reconciliation.
MSC-created debt, cumulative Vat accrual and utilized deductions still need
separate cost adjustments. No zero residual, source certification or resolution
of the previously reported $7.61M/$231k gaps is claimed without a new same-run
calculation. Existing schema-2 snapshots remain evidence, but lack these controls.
