# Spark income statement, v3.8

`GET /v3/reporting/statements?prime=spark&year=2026&cutoff=2026-10-31`

The v3.8 response owns the dashboard's complete row layout. `statement_rows`
contains stable display IDs, labels, parent IDs, depth, row kind and `metric_id`.
Every non-section row points to a member of `lines`, including explicit
unavailable metrics. Consumers render these rows in order, without calculating
new financial subtotals. `display_decimals: 0` requests whole-dollar formatting;
metric strings retain their full calculation precision. Pinned selections retain
both the policy and immutable input revisions. Changes to this layout or
calculation policy require a new policy version.

Revenue covers the declared Spark allocation, fee, configured reward and net
nonstable-revaluation perimeter. Deposits, withdrawals and other principal flows
are excluded. RWA revenue uses the authenticated saved reporting view. Standalone
sUSDS income uses shares and net NAV changes, avoiding the old MSC's distinct
funding adjustments. Sentora RLUSD and SparkLend WETH supply revenue are separate
from issuer incentives and asset-price gains. Sampled daily share/NAV or scaled
balance/index returns retain estimate quality. All family and statement totals
sum disjoint leaf metrics; they do not add a subtotal to its own children.

Anchorage is aggregate verified interest cash received from the reviewed escrow,
with principal returns excluded by the saved receipt attribution. Its recognition
is `receipt`; it is not certified loan-level accrual. The three illustrative loan
rows are removed until actual terms and loan-level balances are confirmed.

October DR remains unavailable until its monthly settlement summary is supplied.
Missing history remains unavailable; no amounts are inferred as zero. Unknown
operating/incentive/distribution expenses remain outside this revenue completion.

## Savings

`GET /v3/reporting/savings?prime=spark&month=2026-10`

`metrics` exposes Savings interest per vault, per asset and in total. `vaults`
provides the capital/interest reconciliation and source pins. Liability balances
are diagnostic balance-sheet quantities and never enter income or expenses as
principal. Depositor interest is deducted once, under funding costs, separately
from Sky funding and net asset-price revaluation.

For each vault, reconstruct supply and chi through Deposit, Withdraw and Drip
events. Verify every Drip's integer diff and the independent closing state.
Monthly interest is emitted Drip interest plus closing undripped interest minus
opening undripped interest. Deposits/withdrawals and share rounding reconcile
separately. Ethereum USDC/USDT/PYUSD/ETH, Avalanche USDC and Robinhood USDG are
included. Stablecoins use par USD. Exact WETH interest is translated by daily
interest quantities at that day's closing ETH/USD quote; USD translation is an
estimate and is distinct from revaluation of the outstanding liability.

## V4 fees

The AMM rows add collected trading fees plus closing uncollected fees minus
opening uncollected fees to the retained capital-adjusted price return. Each
position is authenticated against the hookless configured pool and Spark NFT
ownership. Fee-growth arithmetic uses uint256 wrap and Q128 integer rounding.
Multiple modifications of the same position in one block fail closed until
transaction-level state can be reconstructed. Issuer RLUSD/PYUSD compensation
remains in its own revenue rows and is not included as a trading fee.

## Collection

The regular Spark component publisher includes all new sources. For the October
completion only, run:

```
python -m settle.revenue.spark_reporting_job --from 2026-10-31 --to 2026-10-31 --complete-only
```

This extends the existing reporting document at the saved MSC source's exact
pins. It does not rerun settlement or change MSC rows. The same command can be
used for other retained month ends later, but no historical backfill is included
in this implementation. API requests never invoke RPC, price or event providers.

Consumers hide a row only when every elapsed displayed period has a fully
available exact-zero amount and all its child rows also qualify. Future periods
are ignored; TBD, partial zeroes, losses and nonzero dust stay visible. Filtering
uses full precision before whole-dollar display rounding.

Saved daily source pins seed internal price/index sampling; each candidate is
checked against its timestamp, successor timestamp and the configured finality
margin. An invalid seed falls back to the certified resolver. This reads retained
October inputs and does not create or republish any historical daily revenues.

Arbitrum's bridged sUSDS uses the configured PSM3's `rateProvider()` and
`getConversionRate()` after checking its sUSDS/USDS token identities; it does not
assume the bridged ERC20 implements ERC4626. The valuation follows
[Spark's PSM3 conversion](https://github.com/sparkdotfi/spark-psm/blob/master/src/PSM3.sol#L285)
and the [Spark Arbitrum registry](https://github.com/sparkdotfi/spark-address-registry/blob/master/src/Arbitrum.sol).
Historical market-price collection retries earlier anchors when a provider's
nearest quote falls after the cutoff. Quotes must still be positive, sufficiently
confident, no more than two hours old, and never forward-looking.
