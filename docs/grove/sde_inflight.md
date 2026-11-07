# Async vault deposits, redemptions and historical CoF reconciliation

Atlas [A.2.4.2.3](https://sky-atlas.io/#5d38602e-46e8-48d2-b661-460a3fe9b0b9)
recognizes deposits when the corresponding shares are issued and retains
redemptions until proceeds settle. At each UTC closing block, wallet value,
issued-but-uncollected deposit shares and outstanding redemption value enter
the same SDE ownership cap and active-date rules. Unissued subscription cash
does not qualify for that vault's SDE deduction. A claim paid during the day
is absent at that close. Eligible idle USDS keeps its separate existing rule.

## Shared accounting and protocol adapters

`normalize.async_vaults.AsyncVaultExposure` supplies the same economic shares
to position valuation and the daily SDE calculation. Protocols detect events
differently, but both directions use one exposure identity before caps:

```
economic value = wallet shares × NAV
               + issued, uncollected deposit shares × NAV
               + unpriced/cancelled redemption shares × NAV
               + fixed cash from priced redemptions
```

JTRSY and JAAA resolve `asyncManager()` at the historical block and read the
controller's unfiltered `investments(vault, controller)` state. Its `maxMint`
field identifies allocated issued shares still in escrow. Public `maxMint()`
and `maxDeposit()` views are unsuitable balance sources: compliance transfer
restrictions can make them zero without extinguishing ownership. The same
applies to `maxRedeem()` and `maxWithdraw()`. Both directions now use the
unfiltered manager state; no valuation path uses permission-gated claim views.
Pending unissued subscriptions and cancelled subscription cash are excluded.

Unpriced withdrawals retain `pendingRedeemRequest` shares at NAV. Cancelled
withdrawals retain `claimableCancelRedeemRequest` shares until wallet return.
Once processing fixes the payout, the entitlement is `maxWithdraw` underlying
cash: it is no longer reconstructed into shares or repriced with current NAV.
Partial cash collection reduces only the remaining cash balance. Cash-only
positions do not require a NAV read, including when the wallet is empty.

Collection moves issued shares from escrow to the wallet without changing
economic exposure. Monthly/MTD capital is the vault-event capital amount plus
closing minus opening uncollected acquisition basis. Basis uses the manager's
weighted issuance price and asset-unit rounding, rather than current NAV.
That recognizes issuance once, including partial claims and month boundaries,
without manufacturing income when collection happens later. The same basis
adjustment is applied to every day's Deposit/Withdraw cash flow. Monthly PnL,
average holdings and per-venue CoF attribution all use that economic-capital
series, rather than combining an economic opening position with wallet flows.
For example, 100 already-issued shares collected on the first day of the next
month remain a 100 average holding, rather than being counted twice as 200.
Wallet/event share sanity checks still compare custody flows with balances.

BUIDL deposits already mint directly into the wallet; no pre-mint subscription
cash is added. Its existing subscription-fee accounting remains in place.
BUIDL redemptions retain their matched request/payment adapter below. Basin
idle USDS stays a separate cash deduction, never another deposit-share claim.
No token protocol or empty wallet can bypass the SDE cap or effective dates.

For BUIDL, the shared position adapter now replays that matched ledger at the
exact valuation block. Opening/closing positions, daily SDE and average holdings
all retain the same expected cash receivable through payment. A later receipt
on the same date cannot remove a claim from an earlier pinned position.

Its capital series is the existing share-capital series plus the daily change
in the pending cash receivable. This cancels the same pending-value change in
position valuation, preserving income rather than booking principal twice:

```
economic position = wallet value + pending cash
economic capital  = share capital + change in pending cash
income            = change in economic position - economic capital
                    + existing cash-realization adjustment
```

The expected 99.95% claim mark remains the CoF/position basis. Cash realization
still compares actual proceeds with the request's dated revenue carrying
value; the pre-September fee-recognition exception is preserved. The separate
subscription-fee detector consumes original capital events, rather than
pending-claim reclassifications, so neither genuine fees nor artificial fee
signatures are affected by this bridge. Verified small share exits omitted by
the yield-mint filter are restored as capital in every valued period, including
the August 31 1,000-share test, without changing cash-fee recognition dates.
Frozen settlement inputs are unchanged.

New provenance and the SDE daily workbook split pending exposure into issued
deposits and unsettled redemptions. Older snapshots remain readable; existing
code-version checks prevent finalizing an old calculation under changed code.

## Deposit history replay

`python scripts/reconcile_async_deposits.py` reproduces a **35,889.68 USDS**
marginal CoF credit to Grove, with the opposite impact on Sky and no identified
Spark impact. January contributes 19,731.34, March 13,634.13 and April 2,524.21.
The 13 affected UTC closes are January 14–20 and 22–23, March 11–12 and 30,
and April 3, 2026. All claims completed in their issuance month, so these
cases change daily CoF without changing monthly investment income.

Pinned evidence includes mint receipts, controller claim events and 32 archive
state/NAV samples. JAAA's wallet alone exceeded the 325M cap on every sampled
deposit day, so these deposits do not change its CoF. Spark has no controller
deposit/claim events in the two configured vaults over the fetched history.
The replay reproduces frozen workbook charges and keeps the already-approved
redemption corrections in its comparison baseline, including March 11.

`reconciliation/2026-10/async_deposit_cof.json` records this additional calculation
separately from the accepted 78,530.06 redemption adjustment. It does not
silently rewrite previously approved settlement inputs or assert execution.

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

- **JTRSY / JAAA:** `normalize.async_vaults.async_vault_exposure` reads the
  manager's investment state at each historical closing block. Issued deposit
  shares, unpriced/cancelled redemption shares and fixed redemption cash are
  disjoint components. The old share-only redemption helper has been removed.
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

## Reproducible accepted adjustment

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
The operator accepted the signed CoF adjustment on November 6, 2026. Its
settlement expression is `sky_adj: -78530.06`, reducing Grove's MSC debt mint
rather than creating a separate Send to prime. The October earning-cycle
configuration combines this with the accepted partner rebates: Sky
1,141,145.80 USDS and Grove 915,857.20 USDS. The resulting October adjustments
are `sky_adj: 1062615.74` and `sv_adj: 915857.20`.

The original 2,508.55 credit remains in September's historical adjustment and
is deducted exactly once in the accepted correction. G30 records the operator
acceptance. Acceptance does not establish whether any additional off-report
credits or previous rebate accruals exist; no such overlap has been identified
in the records reviewed. MSC execution is separate from this configuration.
See `settlements/grove/2026-10/reconciliation.md` for the combined bridge.
