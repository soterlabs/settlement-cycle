# Spark monthly revenue API

Spark statements default to `prime-management.spark-income.v3.6`. Other primes
and saved older report selections retain their original policies. These inputs
are isolated from MSC calculations and never rewrite settlement publications.

## Endpoints

| Request | Returns |
| --- | --- |
| `/v3/reporting/statements?prime=spark&year=2026` | Annual monthly statement, source manifest and immutable selection |
| `/v3/reporting/fees?prime=spark&month=2026-10` | SparkLend treasury receipts and monthly accrued Morpho curator fees |
| `/v3/reporting/fees?prime=spark&month=2026-10&venue=S10` | One Morpho vault's fee metric and opening/accrued/received/closing bridge |
| `/v3/reporting/gains?prime=spark&month=2026-10` | Net price gains, gross asset revaluation and depositor offsets |
| `/v3/reporting/allocations?prime=spark&month=2026-10` | Native supply revenue by venue, dollar family totals and observed APR/APY |
| `/v3/reporting/allocations?prime=spark&month=2026-10&venue=S14` | One venue; each Morpho vault also has its own venue ID |

Pass the annual `selection` as `revision` to reproduce exactly the same sources
across all component endpoints. Amounts are decimal USD strings. APR/APY are
ratios (`0.05` means 5%), separate from dollar revenue; sum dollar amounts, never
APYs. Observed rates are annualized from period return and average position,
not forward advertised rates. Empty positions have no invented rate.

Metric `status`, `quality`, `recognition`, `period_start`, `period_end` and source
references describe confidence and coverage. `partial` exposes `known_value`,
not a complete total. Provider failure yields unavailable, never assumed zero.
A failed refresh may retain a same-month amount with its original coverage and
partial status. Earlier saved selections continue to replay without mutation.

## Accounting and coverage

| Line | Measurement | Basis |
| --- | --- | --- |
| SparkLend fees | Seven stable spToken transfers from the reviewed reserve treasuries to Ethereum Spark ALM, at stablecoin par | Received in kind |
| Morpho curator fees | Fee shares minted to configured Spark ALMs at event NAV, plus closing unminted entitlement minus opening entitlement | Accrual |
| Native allocation revenue | Supply return after embedded vault fees; excludes external incentives and Sky/global funding cost | Accrual; historical fallback estimated |
| MORPHO, AVAX, ETH/WETH and SparkLend non-stable price gains | Average daily EOD exposure times underlying price movement, less matching depositor liabilities | Estimated accrual, not FIFO unrealized P&L |

Treasuries: `0xb137e7d16564c81ae2b0c8ee6b55de81dd46ece5` and
`0x856900aa78e856a5df1a2665ee3a66b2487cd68f`. Recipient:
`0x1601843c5e9bc251a3272907010afa41fa18347e`.
The five legacy `rewards.lending.sparklend.*` metric IDs remain for continuity,
but are counted only under fees in v3.6. A difference between a retained
settlement distribution and independently observed receipt-month cash is not
booked as additional revenue: it remains unreconciled, avoiding duplicate
recognition of historical settlement true-ups. spRLUSD and spUSDG complete the seven
stable receipt tokens. Unknown transferred tokens make the fee total partial.

Morpho fee scope:

| Venue | Vault | Recipient |
| --- | --- | --- |
| S10 | Blue Chip USDC | Ethereum Spark ALM |
| S11 | Old Blue Chip USDT | Ethereum Spark ALM |
| S65 | Blue Chip USDT | Ethereum Spark ALM |
| S12 | DAI | Ethereum Spark ALM |
| S13 | USDS | Ethereum Spark ALM |
| S34 | Base USDC | Base Spark ALM |

V1 pending entitlement is inferred from the contract's own ERC4626 dilution;
V2 uses `accrueInterestView`. Authenticated fee events use their event NAV and
all mint/burn events must reproduce closing total supply. Configured percentages
are performance fees, not APR. The RLUSD Morpho vault's different fee recipient
has not been attributed to Spark and is outside this curator scope.

Native revenue covers 27 configured venues: Maple, SparkLend, Aave v3, Morpho,
Arkis and other reviewed vaults. Existing supported publications take precedence;
missing history uses daily balance/index or shares/NAV estimates, stored only as
monthly reporting components. Fluid sUSDS NAV includes the underlying sUSDS NAV.
External rewards stay separate; global funding cost is deducted once below.
This does not establish full entity-wide allocation coverage.

Net SparkLend gains offset spWETH assets by `spETH.assetsOutstanding()` depositor
liabilities. MORPHO covers Ethereum and Base; ETH/WETH covers Ethereum, Base,
Arbitrum, Optimism and Unichain; AVAX covers native and wrapped Avalanche AVAX.
Other reviewed non-stable SparkLend reserves are checked for empty balances and
transfer history; funded unsupported reserves make this component partial.
Daily sampling can miss intraday exposure and is explicitly estimated. Deposits,
reward-token receipt income and index yield are not booked as price gains.
Confirmed supported zero gain rows are hidden; unknown and estimated zeros stay
visible. Existing unclassified gain coverage remains separate.

## Publication and replay

`settle-reporting-spark` in `.railway/railway.ts` runs the independent monthly collector daily at
20:45 UTC. It publishes closed EOM records from January and refreshes the latest
completed MTD day, publishing the newest cutoff first. RPC calls, history and price inputs use the durable input
cache. API reads never call a provider. Components have append-only revisions,
content hashes, code/configuration versions and opening/closing block pins.

Manual replay: `python -m settle.revenue.spark_reporting_job --from 2026-01-01`.
Apply the additive schema before deploying the API (`--schema-only`). Configure
RPC/provider credentials through environment variables; never store them here.
The annual selection's `offchain_revisions` array pins Spark component revisions
in v3.6; Grove's archive selection and older Spark policies retain their semantics.
