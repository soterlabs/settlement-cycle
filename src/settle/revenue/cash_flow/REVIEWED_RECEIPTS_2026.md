# Spark reviewed external receipts — November 2026

These reporting-only bookings use the public transactions captured in
`tests/fixtures/spark_reviewed_receipts_2026.json.gz` and exact retained facts in
`reviewed-receipts.spark-2026.v1.json`. They do not change MSC configuration,
settlement revenue or settlement reports. Unknown payments remain unclassified.

| Reviewed flow | Receipt amount (USD at par) | Reporting treatment |
|---|---:|---|
| April B2C2 returns | 2,098,634 | Internal cash conversions, matched to the three actual 2,100,000 USDC sends using explicit reviewed transaction pairs. The 1,366 shortfall stays unclassified; do not invent interest or a fee. |
| April 3F x Steakhouse USDC redemptions to DevCo and its Safe | 1,114,846.081435 | Reconstructed 1,107,900 principal (investing) and 6,946.081435 realized yield (operating). Recognize native yield monthly over Jan–Apr, not again on redemption. Fallback without the source proof remains provisional. |
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
conversion shortfall and 1,366 B2C2 shortfall are additional **outgoing** unresolved components.
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
claims and treasury vault accruals/splits. Then use `python -m settle.revenue.cash_flow.job --review --from 2026-01-01
--to <latest-completed-day>` with the approved environment supplied privately.
Use `--dry-run --output-dir <local-preview>` to inspect before publishing.
The cash configuration revision changes because the reviewed rule set changes;
do not relabel an old capture as the new scope. Ordinary daily reviewed workers
use the same collection and classification path after deployment.

Verify API source revisions, receipt counts, principal conservation, March SKY
income and its one-time cash bridge, monthly cash subtotals and reconciliation
gaps. The dashboard's unpinned API requests pick up new publications on refresh;
old pinned exports continue selecting their retained source/publication revisions.

## Review safeguards

- A reviewed cash conversion is internal only when both exact cash legs are
  present and their internal amounts offset. Missing or changed legs remain
  unclassified, including operating-wallet sends that would otherwise fall
  through to the generic expense rule.
- An unmatched farm funding transfer or urn cash delivery cannot certify zero
  staking income. The worker retains missing/ambiguous source coverage instead.
- Retained staking receipts must fall strictly after the opening block pin and
  at or before the closing pin. Reward, funding and delivery must share a block
  and timestamp, have distinct log indexes, and cannot reuse any proof log for
  another receipt.

## Reconstructed treasury vault revenue

The DevCo and its Safe deposited 557,900 and 550,000 USDC on January 23.
Their April 9–10 redemptions returned 561,386.572024 and 553,459.509411 USDC.
Reconstruct inception share history, reconcile every owned wallet to independent
opening/closing balances, and value shares using onchain `previewRedeem` at those
boundaries. Monthly native revenue = closing claim − opening claim − subscriptions
+ redemptions. Deposits routed through the bundler require the actual owned USDC
cash leg, not just the share mint. Internal share transfers preserve cost basis.

| Month | Accrued USDC revenue |
|---|---:|
| January | 914.079957 |
| February | 2,602.160431 |
| March | 2,802.664010 |
| April | 627.177037 |
| Total | 6,946.081435 |

November 1–8 also independently reconciles to zero shares and zero revenue;
there was no remaining position. Historical snapshots are in
`tests/fixtures/spark_steakhouse_2026.json.gz`. `investment.steakhouse_usdc` is
API-owned under Morpho vaults. Its monthly accrual is reversed in the indirect
cash bridge, then only realized redeemed yield is added as operating cash.
The full subscription/redemption principal remains investing cash flow.
This is a `reporting_vaults` cash/reporting specification, not an MSC venue.

The annual MSC receivable diagnostic must use the retained
`approved_msc_component`, rather than negating the whole operating income-to-cash
bridge once that bridge includes treasury-vault or staking income. Those streams
are not amounts owed by Sky. Missing approval remains unknown; older saved
publications without separate non-MSC bridges retain their previous projection.
