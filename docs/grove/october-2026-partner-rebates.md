# Grove October 20–21 receipts: JAAA and JTRSY partner rebates

The six Ethereum USDC receipts totaling **2,057,003.00 USDC** are identified as **historical partner rebates**, not principal repayments or Apollo redemption proceeds. The classification comes from the operator-approved partner statement merged in [PR #226](https://github.com/soterlabs/settlement-cycle/pull/226), matched to every actual transaction hash and amount. The source record is pinned at [commit 83a15aa](https://github.com/soterlabs/settlement-cycle/blob/83a15aad01c080245b9a75c673176151b951ca88/reconciliation/2026-10/partner_rebates.json) and copied verbatim to the audit evidence as `approved-partner-rebates.json`.

| Receipt date | Related asset | Individual USDC payments | Total | Allocated to Sky | Allocated to Grove |
|---|---|---|---:|---:|---:|
| October 20 | JAAA | 12 + 499,999 + 486,585 | 986,596.00 | 70,738.80 | 915,857.20 |
| October 21 | JTRSY | 70,409 + 499,999 + 499,999 | 1,070,407.00 | 1,070,407.00 | 0.00 |
| **Total** | | | **2,057,003.00** | **1,141,145.80** | **915,857.20** |

The related venue families are **JAAA (E8 Ethereum / E20 Avalanche)** and **JTRSY (E9)**. The statement does not split the JAAA rebates between its two chains; no per-chain attribution is invented.

The approved detailed allocation is:

- JTRSY: Sky receives 356,999.00 for 2025 and 713,408.00 for 2026 through July 31.
- JAAA: Grove receives 413,320.00 for 2025 and 502,537.20 for 2026; Sky receives 70,738.80 for 2026.

These are historical earnings received in October and carried once through the approved October MSC adjustment, normally settled in November. They are revenue/rebate allocations rather than October-only interest accrual or repayment of invested principal. In particular, they must not be booked as a second Apollo income item. The approved adjustment already allocates 915,857.20 to Grove supply-side revenue and 1,141,145.80 to Sky, before the separate historical CoF correction affects the combined Sky adjustment.

## Why the original Apollo lead was insufficient

All six originate from `0xcd531ae9efcce479654c4926dec5f6209531ca7b`, publicly labelled [Coinbase Prime 1](https://basescan.org/address/0xcd531ae9efcce479654c4926dec5f6209531ca7b). This is a shared custodian payout wallet. It also delivered the previously verified May/August Apollo redemption payments, but that does not make its other payments Apollo-related. Each October transaction is a plain USDC transfer with no fund-identifying memo or redemption identifier.

The follow-up scan found no additional Coinbase-to-Grove USDC payment beyond these six through November 6, and no Plume Apollo holder event from November 1 through November 6. The Ethereum Apollo vault's one event transaction in that window concerns a different holder. These later checks are diagnostic only; the affirmative JAAA/JTRSY association comes from the approved statement, not inference from missing Apollo burns or from tracing pooled Coinbase funds.

## Evidence and accounting limits

- [Approved partner allocation](https://github.com/soterlabs/settlement-cycle/blob/83a15aad01c080245b9a75c673176151b951ca88/reconciliation/2026-10/partner_rebates.json): status `operator_approved`, approval date November 6, receipt-by-receipt asset mapping and allocation.
- [Published October reconciliation](https://github.com/soterlabs/settlement-reports/blob/c285979192d2985586e709bf82722bab58341f6c/reports/grove/2026-10/reconciliation.md): confirms the 2,057,003 total and its use in October MSC adjustments.
- `reconciliation/grove_alm_flows_2026_10/partner-rebate-source.json`: repository commit, Git blob ID, content hash and source URLs.
- `reconciliation/grove_october_2026_coinbase_receipts/`: retained follow-up chain reads, separate from October settlement pins.

The approved source uses detailed statement figures and accepts a 360.00 discrepancy in its final beneficiary totals as an error. This is an approved statement-based allocation, not an independently reconstructed time-weighted entitlement. It also does not itself prove that every rebate was excluded from earlier NAV/income; preserve the existing once-only adjustment and avoid adding it again to the venue revenue snapshot.

The transfer audit now classifies only these six exact, amount-checked transactions as `approved_partner_rebate`. Other payments from the same Coinbase address remain unresolved unless separately evidenced.
