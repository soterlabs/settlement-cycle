# Spark AMM revenue

`GET /v3/reporting/amms?prime=spark&month=2026-09`

Optional `venue=S61` filters one pool. Optional `revision` pins the annual
statement selection. The same five metrics appear under allocation revenue in
`/v3/reporting/statements` using `prime-management.spark-amms.v3.7`.

| Venue | Family | Pool |
|---|---|---|
| S61 | Uniswap v4 | PYUSD / USDS |
| S62 | Uniswap v4 | USDT / USDS |
| S66 | Uniswap v4 | RLUSD / USDS |
| S24 | Curve | sUSDS / USDT |
| S25 | Curve | PYUSD / USDS |

Revenue is closing position value minus opening position value minus net capital
added, less the published Sky share. Deposits, withdrawals, principal, external
rewards and global funding costs are not LP revenue. Signed losses are retained.
The source is the exact saved raw result behind the financial publication, or
the selected immutable monthly settlement summary when no raw pool row exists.
Historical monthly summaries are rounded to cents. No provider calls or daily
settlement replay are required at read time.

Curve trading fees accrue through LP-token value. S24 also earns embedded sUSDS
yield: MSC reclassifies this outside the SDE-eligible return. When that saved
adjustment reconciles the position bridge, reporting includes it as LP revenue,
with `embedded_susds_return_usd` in the ledger and
`sky_share_excluded_income_usd` in the allocation split. Sky's saved share applies
only to the remaining eligible return. Curve periods with capital movements
retain `quality: estimate` because the source prices those movements at the
ending LP unit value rather than individual transaction prices.

Uniswap v4 positions are NFTs:
the current source values the underlying liquidity but does not reconcile all
collected and uncollected trading fees. Consequently Spark-retained v4 returns
are `partial`, with `known_value` and `quality: estimate`; they are not complete
accrued fee revenue. A fixed 100% Sky share produces zero Spark-retained
SDE-eligible revenue under the published treatment. Unknown source data remains unavailable.

`pools` contains the capital bridge, Spark/Sky split, excluded external rewards,
fee coverage and source reference. `metrics` uses the standard reporting status,
recognition, quality and provenance fields. A stale source produces a partial
metric. Existing report selections retain their original policy and values.

RLUSD issuer compensation remains in `rewards.issuer.rlusd` (S67). Its configured
earning balances already include the RLUSD leg of S66. PYUSD compensation also
remains separate (S68). These issuer payments are distinct from AMM trading fees;
neither is added again as an LP reward. Settlement/MSC results are unchanged.
