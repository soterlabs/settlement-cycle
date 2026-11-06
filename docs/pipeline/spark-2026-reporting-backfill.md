# Spark 2026 reporting history

Backfill completed calendar months and the latest completed UTC day of the
current month. Future months retain their future state; unavailable inputs
remain unavailable. This operation publishes independent reporting components,
not MSC results or daily revenue sums.

## Sources

| Item | Historical source |
| --- | --- |
| DR, AR, Sky cost of funds | Existing immutable published monthly summaries |
| Supply revenue | Native reporting components; a published monthly native-return fallback is explicitly estimated and excludes capital and external rewards |
| SparkLend fees, Morpho curator fees, asset revaluation | Pinned token events, balances, fee checkpoints and USD quotes |
| sUSDS | Reconciled Ethereum share flows and chi/rho/ssr events; each L2 uses its local PSM3 oracle |
| Savings funding cost | Reconciled Drip interest and pending interest; user capital is excluded |
| Uniswap v4 | Complete owned NFT inventory, fee accrual, event-price capital and receipt reconciliation |
| RLUSD / PYUSD incentives | Existing ACT/365 daily EOD eligibility and issuer-rate rules, calculating only the requested month's income |
| USDG incentives | Configured eight-hour reward receipts |
| Anchorage | Configured cash-attribution method, applying registered principal-return exclusions; no loan terms or loan accrual is inferred |

The sUSDS revenue is the full return on the ALM's position. Savings depositor
interest appears separately as funding expense, rather than being deducted
again inside that allocation's income.

Contract absence is proved with historical code and event evidence before a
zero is published. Months before Robinhood's first real block use the verified
genesis boundary, rather than a future block. Existing historical block
candidates are only accelerators and must pass fresh RPC cutoff/finality checks.

## Run and resume

The standard source job can populate a historical window:

```sh
python -m settle.revenue.spark_reporting_job --from 2026-01-31 --to 2026-11-03
```

Use the actual latest completed day rather than the example's end date. For
months that already have reporting components, `--complete-only` extends those
immutable sources without requiring an old MSC snapshot. Each publication is
append-only and independently resumable. Keep provider credentials in the
existing environment. Capture the actual code/config versions when publishing.

The one-time 2026 collection reuses available components and published monthly
summaries and calculates missing reporting components at month boundaries.
Data is checked and published month by month. Re-run failed components without
replacing successful calculations with zero.

## API and dashboard

`prime-management.spark-history.v3.9` adds historical monthly-source fallbacks
and independent monthly issuer/Anchorage sources. Earlier v3.8 and other pinned
selections retain their original values and missing cells. Per-venue allocation
and AMM endpoints use the same completed values as the statement.

The dashboard accepts both versions, fetches current live responses and refreshes
visible pages every 30 seconds or when focus/connectivity returns. New available
cells replace TBD without resetting filters or expanded rows. Estimated monthly
fallbacks retain estimate coloring. A failed refresh keeps the last validated
response and its retained-data treatment; saved exports remain pinned.
