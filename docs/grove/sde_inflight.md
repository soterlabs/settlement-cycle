# SDE redemption exposure and historical CoF reconciliation

Atlas [A.2.4.2.3](https://sky-atlas.io/#5d38602e-46e8-48d2-b661-460a3fe9b0b9)
retains a redeemed position until proceeds settle. At each UTC closing block,
held value plus outstanding redemption value enters the normal SDE ownership
cap and active-date rules. A claim paid during the day is absent at that close.

## Why JTRSY automation did not repair earlier MSCs

PR #179 (commit `ef0e1b2`, September 2026) added historical pending/claimable
share reads to both position valuation and the daily SDE deduction. It ran for
August and corrected August 31. Its investigation said August was the only
affected month, overlooking gaps that opened and closed inside April and May.
Those reports were last refreshed before that fix; their Debt tabs exclude the
pending JTRSY value. Neither applying the fix to later months nor checking only
month-end positions repairs their daily CoF. The earlier reconciliation posts
compare baselines which also predate this fix. Reconciliation III identifies
only BUIDL fees and August 31 BUIDL CoF, not these JTRSY windows.

The pinned evidence finds 14 JTRSY windows spanning 21 UTC closing balances:
April 23-24; May 1-4, 4-5, 11-12, 13-14, 14-15, 15-18, 18-19, 19-20,
20-21, 21-22, 22-26, 26-27 and 27-28. August 31 JTRSY is already in the
published August baseline and must not be added again.

## Sources and common calculation

`compute.monthly_pnl._sde_asset_value_timeseries` combines the sources below
before computing the daily SDE deduction. Daily diagnostics retain wallet,
pending, economic uncapped, and actual deducted values in provenance and the
workbook. Both daily/MTD and monthly production runs use this function.

- **JTRSY / JAAA:** `normalize.positions._centrifuge_in_flight_shares` reads
  `pendingRedeemRequest(0, controller)` plus `claimableRedeemRequest(0,
  controller)` at each historical closing block. They are disjoint states.
  A fully escrowed position is still read even when wallet balance is zero.
  Failed deployment probes and failed reads stop the run; only a successful
  pre-deployment check permits zero pending exposure.
- **BUIDL:** `normalize.redemption_settlements` reuses its verified share-exit
  and USDC-payment filters and unique/exact-group matcher to track pending
  claims. There is no T+1/T+3 age limit. Same-day payments disappear that day;
  weekend and month-boundary claims persist until payment. Requests already
  settled before the report are consumed before evaluating the first day.
  The fixed empty-history anchor is Grove inception, May 14, 2025. Cash-PnL
  recognition still starts September 1, 2026. Earlier fees are not rebooked.

BUIDL pending claims use the contractual 99.95% expected exit mark, not future
cash receipts. This preserves the $24,986,500.50 basis used for the recorded
August 31 credit. Cash realization still uses the request's dated carrying
value, then books the difference from actual receipts; this is independent
of CoF. The known ambiguous August 24 one-share-plus-49,999,999-share batch
has exact transaction/log links. New ambiguous groups or unexplained material
issuer cash fail rather than silently dropping a receivable or guessing.

No BUIDL CoF dates need to be enumerated in `sky_direct_exposures.yaml`.
Legacy manual windows remain available for other venues without a ledger;
automatic and manual claims cannot be combined for the same venue.

JAAA's March scalar dates identify the retirement of Sky's $325M tranche,
which is ownership metadata rather than a list of ordinary redemptions.
The known cash date now excludes March 11 from that tranche's deduction.
Its six January pending requests leave at least $453.37M on-wallet, so the
full $325M deduction remained available and no January CoF credit is due.
Without a known cash date, the legacy formal SDE end date remains inclusive.

## Reproducible proposal, not a booked payment

Run offline from the repository root:

```sh
python scripts/reconcile_sde_inflight.py \
  --output /tmp/sde_inflight_cof.json
```

The script verifies SHA-256 hashes of the original monthly workbooks, retains
their daily APY/APR conventions, rates, balances and subsidy cap, and reproduces
every frozen daily charge to within 0.000001 USDS. It applies all venues' changes
on the same day together through the shared `daily_borrow_charge`, preserving
nonlinear subsidy-tranche effects. It does not rerun whole historical months
using current methodology, publish API revisions, or modify MSC instructions.

Evidence and output:

- `tests/fixtures/sde_inflight/buidl_events.json`: filtered request/cash logs,
  with transaction/log identities, through September 30.
- `tests/fixtures/sde_inflight/centrifuge_history.json`: 40 matched vault
  requests/withdrawals, 36 historical pending-state checks, precise JTRSY NAV
  reads, and original workbook hashes/pins. All 36 states agree with event
  reconstruction. Earlier cross-chain and secondary-market share transfers
  are not mislabeled as redemptions.
- `reconciliation/2026-10/sde_inflight_cof.json`: signed per-day changes and
  monthly credits. Positive credit is relief to Grove; negative is a debit.

| Period | Component | Additional credit to Grove (USDS) |
|---|---|---:|
| March | Remove JAAA's extra settlement-day deduction | -33,417.95 |
| April | JTRSY pending exposure | 5,147.69 |
| May | JTRSY and BUIDL pending exposure, calculated together | 68,481.23 |
| August | BUIDL pending exposure, less recorded 2,508.55 credit | 5,017.29 |
| September | BUIDL pending exposure | 33,301.81 |
| **Total from unrounded daily values** | | **78,530.06** |

Monthly display rounding can differ by one cent from the unrounded total.
The proposed settlement expression is `sky_adj: -78530.06`, reducing Grove's
MSC debt mint rather than creating a separate Send to prime. **It is not added
to settlement configuration.** Confirm the March settlement-day interpretation
and all executed or off-report corrections with Grove before booking (G30).
The original 2,508.55 credit remains in the September historical adjustment
and is deducted exactly once in this proposal. Other BUIDL fee corrections,
rebates, and fee markdowns are outside this CoF calculation.
