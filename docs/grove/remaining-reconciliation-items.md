# Grove: warehouse principal, Basin reporting and small BUIDL costs

These changes support a fresh, isolated reconciliation run. They do not change
rate formulas, regenerate published settlements, or add amounts to a future MSC
earning cycle automatically.

## Warehouse principal

E42 now uses the existing `notional_principal_usd` schedule to reflect actual
September funding to its configured principal relay:

| UTC date | Additional principal | Principal after funding |
|---|---:|---:|
| September 10 | $1,000 | $304,001,001 |
| September 11 | $23,749,000 | $327,750,001 |
| September 12 | $47,500,000 | $375,250,001 |

September's average is **$349,916,701**; closing principal is **$375,250,001**.
This includes the original $1 July test. The configuration before September
is unchanged. The [September audit](september-2026-venue-audit.md) identifies
all three transactions. Its offline replay now compares every September
schedule transition with net relay funding, using pinned block hashes and UTC
dates; it fails if they differ.

This changes the per-venue allocation of CoF, not aggregate CoF or investment
income. The debt calculation already charged interest on the funded capital.
The ordinary revenue calculation continues to use actual warehouse payments.

## Basin reporting

The September 1 idle-USDS exemption was already applied to Grove's aggregate
borrowing charge. Its reporting attribution now comes from the **same daily
historical snapshots**: actual Basin/pocket cash, internal ownership shares,
and the single GROVE-A debt cap across both Basins. It performs no extra RPC
reads and leaves the aggregate exemption unchanged.

The snapshots identify other LPs' cash separately. Report CoF weights remove
that cash because it is not Grove principal, then remove Grove's actual
already-exempt idle cash. Only the latter is reported as a utilized deduction;
other LPs' cash does not create another borrowing-cost credit. Invested
collateral, USDC and unrelated/direct ALM balances are not blanket-exempted.
If the compartment cap binds, Grove's non-exempt owned cash retains its CoF
weight. A fully exempt pocket has zero allocated CoF and no artificial loss.

The time-weighted amounts are preserved in `MonthlyPnL.basin_cash_reporting`
and provenance, and consumed by the existing `compute_sheet_rows` report
allocator. The report retains its existing headline reconciliation identities.
Older snapshots remain readable with empty reporting metadata; a fresh
calculation is needed to obtain the new attribution. No exemption is inferred
when merely rerendering old provenance. The pre-September policy remains
unchanged.

## Small historical BUIDL cash costs

`python scripts/reconcile_grove_buidl_fees.py` reuses the production redemption
matcher against the existing inception-to-September event evidence, but opens
a **local historical recognition window** ending August 31. The production
September 1 recognition date is unchanged. Every matched cash settlement is
included, regardless of request size. The May 1,000-share test and August's
one-share grouped test are already represented in the original fee credit.

| Component | USDS |
|---|---:|
| Exact January–August realized cash costs | 162,506.171229 |
| Fee credit already recorded (excluding CoF) | 162,505.350000 |
| Additional fee cost, before rounding | 0.821229 |
| Reversal of artificial small-share principal losses in published income | 4,001.000000 |
| Net additional Sky adjustment, before rounding | 4,000.178771 |
| **Proposed additional `sky_adj` — debit to Grove** | **+4,000.18** |

The August 31 1,000-share test returned 999.178498 USDC, realizing 0.821502
USDS of cost. The difference between that amount and the fee residue is
rounding already embedded in the May/August credits. However, the fee residue
alone is **not the net reconciliation**.

Published May's E10 capital inflow is -274,997,000, omitting three 1,000-share
exits; August's is -74,998,999, omitting the one-share grouped test and August
31's 1,000-share exit. Those omissions already reduced Sky's SDE income by
3,000 and 1,001 respectively. The production capital restoration added by
#226 reverses those artificial losses. Its historical income effect is not
part of the separately approved CoF-only correction. The correct comparison
nets that 4,001 debit against the remaining 0.821229 cash cost.

The script verifies immutable hashes of the May/August published summaries,
reconciles their reported income with position changes and capital, and
compares the capital flow with the verified requests. Both summaries were
also checked against the identical files in `settlement-reports`. It refuses
an unexplained capital baseline and does not debit already-restored capital.
The May fee test must not be credited twice. Monthly differences are summed
**before** rounding the final net adjustment.

The [replay artifact](../../reconciliation/grove_small_buidl_fees_2026.json)
records all matches, input hashes, monthly costs, prior fee credits and
capital corrections. It is calculated, **not booked**. It excludes the
already-recorded CoF credits, September cash realizations (including the large
August 31 request paid September 1), and the September haircut transition.
The net adjustment would increase Grove's MSC debt mint under the existing
Sky adjustment treatment; it is not another direct payment to Grove.

Do not add the 4,001 capital-repair delta from a separate historical rerun
again when using this net proposal. No additional off-report capital true-up
was identified in the reviewed records; check for any such credit/debit before
booking the next cycle.

## Running the reconciliation

With normal source credentials, use a new output root to preserve published
reports and review September's refreshed attribution:

```sh
python scripts/run_live_2026.py --primes grove --months 2026-09 \
  --output-root /tmp/grove-followups
```

Offline evidence checks:

```sh
python scripts/audit_grove_september_2026.py
python scripts/reconcile_grove_buidl_fees.py
```

Warehouse and Basin corrections redistribute existing CoF. They must not be
summed as new headline credits alongside the net BUIDL proposal. Any later
MSC bridge must also keep the separately accepted in-flight CoF correction and
partner rebates distinct, and compare against prior recorded adjustments.
