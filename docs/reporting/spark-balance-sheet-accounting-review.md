# Spark balance-sheet accounting: review scope

17 November 2026. Reporting only; this change does not amend MSC calculations or published settlement reports. Chain evidence establishes balances and transfers. Ownership policy and legal entitlement require their own sources.

## Included

- Exclude historical SDE legs from Spark assets and offset the same values against debt. Keep gross on-chain debt and the deduction visible in a memo, outside liability totals. Missing ownership evidence makes net debt unavailable.
- PSM3: value the ALM's USDC share as `USDC balance × ALM shares / totalShares`, at each chain's closing block; retain USDS/sUSDS in Spark assets. This is a balance-sheet ownership calculation, not a new borrowing-cost venue.
- Split uncollected Uniswap fees by the closing principal value of Sky's and Spark's legs. Preserve gross pool value and show the split in retained evidence. This is a closing-claim convention; a historical income split must use ownership during the earning period.
- Add the Arkis SAFE at **provisional $4m cost**, contingent on successful payment evidence. This is separate from the Arkis lending vault. Legal entitlement and impairment still need confirmation.
- Preserve all Savings vault liabilities and controlled-holder eliminations, small holdings, and cumulative issuer reward accrual less receipts (negative = prepaid). These already exist; no new minimum holding threshold.
- Preserve previously pinned statement publications. New rows/offsets apply only to newly collected accounting snapshots.

## Historical SDE policy

Source: [Atlas, commit 6daa019](https://github.com/sky-ecosystem/next-gen-atlas/blob/6daa01968a0fc3fe67fdedc45761cf33ba114515/content/A.2%20-%20The-Support-Scope.md).

| Leg | Start | Last included day |
|---|---|---|
| PSM3 USDC, non-Ethereum | 13 Nov 2025 | Ongoing |
| Curve sUSDS/USDT: USDT | 13 Nov 2025 | 25 Jun 2026 |
| Uniswap USDS/USDT: USDT | 26 May 2026 | Ongoing |

Dates are inclusive UTC calendar dates. MSC's simplified venue configuration is not used to override these reporting dates.

## September reconciliation

Retained September closing stocks were compared with fresh historical PSM3 getters and the Arkis transaction receipt. LP fees use the independently reconstructed September fee-growth observations. Values below are USD, rounded only for display.

| Item | Amount |
|---|---:|
| Gross on-chain debt | 3,686,615,117.41 |
| PSM3 USDC excluded from Spark assets/debt | 12,036,805.62 |
| S62 USDT principal attributable to Sky | 40,036,067.42 |
| S62 uncollected fees attributable to Sky | 849.01 |
| Total SDE deduction | 52,073,722.06 |
| Reported net debt to Sky | 3,634,541,395.36 |
| S62 uncollected fees attributable to Spark | 211.30 |
| Provisional Arkis SAFE | 4,000,000.00 |

Arkis payment: [2 Dec 2025 transaction](https://etherscan.io/tx/0x742a35dbd975d28770014edb1a55e52597555a6ca801c80980230651fde33ab3), Foundation `0x92e4629a4510af5819d7d1601464c233599ff5ec` to `0xd5ff8bdef23fc3c8ff6815c6b4051f9809c877a5`. The collector stores receipt and canonical block evidence; it does not infer SAFE ownership from arbitrary transfers to that recipient.

## Remaining work before publication

1. **Gross MSC receivable/payable:** replace the existing net balance with separate obligations. Recognise the earned month's accrual at month-end; an entry made on the first day of the following month still belongs to that closing period. Reduce each leg on its actual chain execution date. A correction needs an explicit accounting period; that was the meaning of “effective cycle.” Complete opening obligations and execution history are required. This PR does not claim that ledger is ready or change the existing MSC rows.
2. **Trading-fee income:** current `spark_ownership.py` assigns S62's post-designation return to Sky. Reconcile that income split using holdings over the earning period before publishing the revised closing claims. Do not use a month-end weight for all historical income. This PR changes closing claims only; keep it draft until this alignment is resolved.
3. **Equity:** opening equity and token-award valuation remain pending. The confirmed $20.6m genesis transfer alone does not establish the additional $4.4m advance. Reviewed buybacks need complete opening-to-closing coverage before a cumulative equity row is published. No balancing plug or invented zero.
4. **SAFE:** obtain the supporting agreement and confirm impairment treatment. The user approved provisional cost while this question is pending.

The existing reporting dashboard renders API rows dynamically; no UI change is included. A separate, reviewed refresh is needed after merge. No production refresh is triggered here.

## Issuer terms supplied by Spark

Daily EOD balances, ACT/365. RLUSD: 4.5% APR on idle ALM RLUSD, unborrowed SparkLend supply and open v4 RLUSD legs; Morpho receives only `max(4.5% − fee-net vault APR, 0)`. PYUSD: convert APY `DFEDTARL − 0.625% + 1.8%` to APR with `365 × ((1 + APY)^(1/365) − 1)`; spread 2.575% before 9 March. Apply to idle, unborrowed lending and open v4 legs; Curve eligibility ends in June. Confirm the applicable historical year/terms before extending coverage. USDG receipts every eight hours approximate accrual at cut-off.

Outstanding claim = opening supported claim + earned accrual − receipts. A negative supported balance remains prepaid; absence of full historical terms is a coverage gap, not proof that a liability should be deleted. These terms do not change cash-based MSC revenue.

ALM: `0x1601843c5e9bc251a3272907010afa41fa18347e`. RLUSD payer: `0xb70e0fad7d0cf4c187365a84e728802ab24ca78b`; PYUSD payer: `0xfc0539d019482d311c161ae3b756cdccdec45e87`. Morpho RLUSD: `0xfc8c624b6080a0a780583799f2a862de936f6e22`.
