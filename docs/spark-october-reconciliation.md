# Spark reporting reconciliation — October 2026

Scope: reporting sources for 1–31 October only. MSC inputs and settlement
amounts are unchanged. No earlier monthly revenue has been recalculated.

| Revenue or cost | Method | October USD, rounded | Confidence / basis |
| --- | --- | ---: | --- |
| sUSDS, Ethereum + Arbitrum | Closing asset claim less opening claim and actual share capital flows; Arbitrum uses its local PSM3 oracle | 2,931,214 | Supported accrual |
| SparkLend RLUSD | Reconstruct scaled mutations at each event's liquidity index; reconcile `scaledBalanceOf` and `balanceOf` | 42,217 | Supported accrual |
| SparkLend USDG | Same reconstruction, including reserve-token receipts as capital | 0 | Supported accrual; zero proved |
| SparkLend WETH supply | Same reconstruction; translate each day's earned WETH at its daily closing USD quote | 63,434 | Supported accrual |
| Sentora RLUSD | Actual ERC4626 deposit/withdrawal assets; closing less opening claim, excluding capital | 40,810 | Supported accrual, net of embedded fees |
| Fluid Base | Unchanged fsUSDS inventory, full wrapper claims and local Base PSM3 sUSDS conversion | 1 | Supported accrual |
| Uniswap v4 PYUSD/USDS | Event-price liquidity principal, checkpoint fee accrual and whole-receipt cash reconciliation | 5,573 | Supported investment return |
| Uniswap v4 RLUSD/USDS | Same; includes newly discovered October NFT 460903 | 12,006 | Supported investment return |
| Uniswap v4 USDT/USDS | Same; includes October NFTs 485852 and 489097; saved 100% Sky share retained | 0 | Supported Spark return |
| MORPHO price revaluation | Constant inventory proved on Ethereum and Base, multiplied by closing less opening USD price | -9 | Supported accrual |
| AVAX / WAVAX price revaluation | Zero opening/closing inventory, no wrapped-token movements, no native activity in a finalized index covering October | 0 | Supported accrual; zero proved |
| SparkLend non-stable price revaluation, net | Daily capital and interest bridges for spWETH and Savings ETH outstanding debt; offset depositor FX | 5,981 | Supported under the daily closing convention below |
| Savings vault depositor interest | Contract Drip amounts plus closing pending interest less opening pending interest, excluding user deposits/withdrawals | 2,946,959 | Supported accrual; ETH component 71,633 |
| Native ETH / WETH price revaluation | Existing daily exposure estimate | Less than 0.01 loss | Remains estimated: native-transfer coverage on Base and Arbitrum is not certified |

## USD translation and net ETH gains

Recognize daily WETH supply interest, Savings ETH interest and capital movements
at that day's closing USD quote. Quotes must be positive, at least 90% confidence,
not later than the cutoff and at most two hours old. Token-unit interest is
reconciled separately from USD translation.

Under this convention, daily FX revaluation is the opening token claim multiplied
by the change in closing USD quotes. Net SparkLend FX subtracts the same calculation
for Savings depositor debt. Each day's asset and liability claim reconciles:

`closing claim - opening claim - net capital = token interest`

Savings debt additionally reconciles `totalAssets - idle WETH = assetsOutstanding`.
The complete idle-WETH transfer ledger bridges each day's cash balance. Capital
and interest translate at the closing quote; neither enters the price-gain line.
This is an explicit daily recognition convention, not an intraday execution-price
P&L or an average of opening and closing holdings.

## AMM controls

Discover position ownership from ERC721 history to establish October's opening
inventory and new positions; this identity lookup does not calculate prior-month
revenue. Verify pool identity, ALM ownership and liquidity checkpoints. Price every
liquidity mutation using its preceding Swap event or the block's opening price,
including the contract's deposit/withdrawal rounding rules.

Across 119 October receipts, independently calculated liquidity principal minus
collected fees, plus separately identified swap cash, reproduces the exact ERC20
cash settled with PoolManager. Multiple configured pools in one transaction are
checked together. Earned fees equal collections plus closing pending fees less
opening pending fees. Issuer incentives remain outside LP returns. Their existing
NFT discovery already includes the new October positions.

## Totals and remaining gaps

| October total | USD, rounded | Availability |
| --- | ---: | --- |
| Allocation revenue, including allocation-linked rewards | 13,588,787 | Available; contains intentional cash-basis components |
| Cost of capital | 13,240,117 | Available |
| Net interest income | 348,670 | Available; mixed accrual/cash basis |
| Known noninterest revenue | 898,517 | Partial: DR pending |
| Known total net revenue | 1,247,187 | Partial: DR pending |

Totals sum their underlying full-precision amounts. October DR is unavailable,
not zero. Cash receipts remain cash basis (blue); mixed-basis parents retain that
classification. The sub-cent native ETH/WETH gain stays estimated (orange).
Expenses outside this revenue reconciliation retain their existing availability.
Earlier pinned statement selections reproduce their original report hashes.
