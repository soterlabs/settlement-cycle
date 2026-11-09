# Spark reviewed external receipts — November 2026

These reporting-only bookings use the public transactions captured in
`tests/fixtures/spark_reviewed_receipts_2026.json.gz` and exact retained facts in
`reviewed-receipts.spark-2026.v1.json`. They do not change MSC configuration,
settlement revenue or settlement reports. Unknown payments remain unclassified.

| Reviewed flow | Receipt amount (USD at par) | Reporting treatment |
|---|---:|---|
| April B2C2 returns | 2,098,634 | Investing allocation returns; also classify the three actual 2,100,000 USDC advances. The 1,366 difference remains in the boundary; do not invent interest or a fee. |
| April 3F x Steakhouse USDC redemptions to DevCo and its Safe | 1,114,846.081435 | Gross investing redemption, provisional until share cost basis is reconciled. An authenticated principal/yield witness overrides the provisional booking. The receipt is not entirely revenue. |
| January Maple-related payment | 383,178.08 | Operating cash realization of operator-confirmed yield. No new income-statement accrual on top of existing Maple supply revenue without a separate earning-period reconciliation. |
| February–March Ops nonstable sales through CoW | 382,428.427806741322142644 | Investing treasury disposal proceeds. Same-transaction sold tokens: weETH, cbBTC, wstETH, rETH, tBTC and WBTC. Full sale proceeds are not fresh revenue; this change does not manufacture missing realized gain/basis data. |
| March DevCo USDS → USDT conversion | 47,520 USDT received | Internal cash conversion. Match 47,520 on each side; preserve the 2.902265232988598019 USDS shortfall as unclassified instead of assuming a fee or an operating expense. |
| March Lockstake USDS farm reward | 2,095.067540312875692237 | Receipt-basis allocation revenue under **SKY staking rewards**, plus operating cash realization. The indirect cash bridge reverses the same income component before adding the delivery, preventing double counting. |
| August Binance OTC return | 2,249.428190 | 2,000 investing principal and 249.428190 unclassified. Classify both actual 2,000 USDT advances as investing principal; the second advance remains outstanding under the existing boundary convention. |

A booking matches chain, transaction, block, timestamp, log index, token,
sender, recipient and signed amount. Another payment from the same address
cannot inherit it. Split amounts conserve the original Transfer exactly.
Payer labels or matching amounts alone are never proof of new income.

## Ongoing SKY rewards

The source worker reads the reviewed Lockstake urn
`0x74bf5ab1c53e7b5ccec8d7107a76aea4e058cdf7` and USDS farm
`0x38e4254bd82ed5ee97cd1c4278faae748d998865` over the pinned monthly interval.
Each recognized claim needs `RewardPaid`, the matching farm → urn USDS transfer,
and the matching urn → owned-wallet cash transfer. Missing or ambiguous legs
leave the source unavailable; no estimated accrual schedule is inferred.
Coverage is this reviewed urn, not a claim that every possible SKY position is
tracked. Failed refreshes retain prior same-month amounts as partial with their
original coverage end.

New source publications add `investment.sky_staking` to allocation revenue.
Older retained source selections without the component keep their old layout
and amounts. The API owns the new row, hierarchy and cash classifications;
the dashboard already consumes them without a UI change.

## Unresolved items

The 4,686,868.703083 USD ordinary-receipt audit remainder stays unclassified:
exchange withdrawals and external Safe payouts need account/ownership/purpose
confirmation; the 8,502.754893 and 2,400 USDC possible refunds need confirmation.
The OTC excess is included in that remainder. The 2.902265232988598019 USDS
conversion shortfall is an additional **outgoing** unresolved component.
Sub-dollar receipts remain in evidence. Zero-address mint/bridge/debt bookkeeping
requires separate transaction-level pairing and is not promoted to revenue.

The 4.03m identified **gross receipts** are not a predicted reconciliation-gap
improvement. Paired advances, conversions, income reversals and opening balances
matter; retain all ledger legs when comparing monthly gaps.

Evidence context: `docs/spark/b2c2-boundary-2026-10-08.md`,
`docs/spark/binance-otc-tests-2026-10-09.md`, and
`docs/spark/remaining-receipt-questions-2026-10-09.md`.

## Publication after merge

This PR does not mutate the reporting DB. Publish reviewed source components
first, then replay the reviewed cash-flow publications for Jan–Oct month ends
and the latest completed November day. The source review refresh
(`python -m settle.revenue.spark_reporting_job --help`, `--review-inputs`)
preserves existing native/issuer accruals while adding authenticated staking
claims. Then use `python -m settle.revenue.cash_flow.job --review --from 2026-01-01
--to <latest-completed-day>` with the approved environment supplied privately.
Use `--dry-run --output-dir <local-preview>` to inspect before publishing.
The cash configuration revision changes because the reviewed rule set changes;
do not relabel an old capture as the new scope. Ordinary daily reviewed workers
use the same collection and classification path after deployment.

Verify API source revisions, receipt counts, principal conservation, March SKY
income and its one-time cash bridge, monthly cash subtotals and reconciliation
gaps. The dashboard's unpinned API requests pick up new publications on refresh;
old pinned exports continue selecting their retained source/publication revisions.
