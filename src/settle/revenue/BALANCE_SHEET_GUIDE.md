# Spark balance sheet — first slice

Read-only endpoint: `/v3/reporting/balance-sheet?prime=spark&year=2026`.
Optional `cutoff=2026-10-31`, or `revision=<selection>` for an immutable export.
The endpoint reads saved reporting publications only. It triggers no collection,
backfill or settlement writes. Existing 2026 snapshots supply the initial history.

## Stock versus flow

A balance sheet measures closing **principal plus earned yield**. An income
statement measures revenue and expenses. A cash flow statement measures movements.
Never sum closing balances into YTD or derive outstanding principal by summing
cash flows without an independently verified opening balance and noncash changes.
`Latest` copies the latest elapsed period, including its missing-data status.
It never falls back silently to a previous complete month.

| Line | Saved source / treatment |
|---|---|
| Cash and cash equivalents | Cash-flow `measured-cash`, using the same controlled wallets, token registry and closing block pins. Retain missing-chain coverage. |
| Allocation assets | Explicit mapped reporting venue **closing position values**. Group as SparkLend, Aave, Morpho, Maple, Arkis, RWA vaults, Anchorage, AMMs, sUSDS and earning stablecoins. Include principal; exclude raw cash venues already counted in cash. |
| 3F x Steakhouse USDC | Validated treasury-vault `closing_value`, not monthly revenue or cumulative deposits. |
| Non-stable treasury assets | TBD until closing holdings/valuation evidence is composed; monthly gains are not closing asset values. |
| MSC receivable / payable | Signed closing approved MSC control from the cash-flow projection, reclassified to its positive asset or liability side. A partial entitlement/receipt control stays TBD rather than certifying a balance or its sign. Treasury-vault and staking revenue are not owed by Sky. |
| Accrued issuer rewards | Positive RLUSD/PYUSD/USDG earned-minus-received balances. These are separate from the received tokens still held as allocations. Do not add vault-embedded yield again. |
| Prepaid issuer rewards | Negative earned-minus-received balances, shown as positive liabilities. Do not clamp an advance to zero or book it again as income. |
| Savings depositor liabilities | Gross closing vault claims (principal **and** interest), translated at the closing USD price for ETH. Reporting savings venues are liabilities, not additional allocation assets. Controlled-holder share eliminations remain TBD until proven. |
| Sky debt | TBD. Net draws are not proof of outstanding debt; require an independently pinned debt balance and the approved Sky-direct ownership treatment. |
| Other assets, receivables and payables | TBD where the closing balance is unsourced. Expense accruals minus payments alone do not establish opening payables. |
| Current-year net income | Cumulative published cash-flow `net-income`, retaining the reporting-income/approved-expense dependency, missing months and estimates. This is the sole flow intentionally accumulated into an equity component. |
| Contributed capital, reserves and opening retained earnings | TBD until independently sourced. Contributor own-token compensation may also require an equity credit; transfer/claim quantities do not establish grant valuation. Never use the balancing residual as equity. |

## Perimeter and dates

* Use reporting-enabled positions only. Savings claims, raw cash and issuer accruals
  have separate roles; never sum every row returned by the venue API as assets.
* Do not treat an AMM's revenue share as its closing ownership share. A nonzero
  Uni v4 USDT/USDS balance, and historical designated Curve USDT exposure, stay
  unavailable until closing Sky/prime ownership is independently proved. Do not
  designate PYUSD/USDS or RLUSD/USDS as Sky direct merely because they are AMMs.
* Historical columns require exact month ends. MTD prefers a common cutoff across
  venue, cash-flow and supplemental publications. If no common cutoff exists,
  expose only sources at the selected date; never mix dates within one closing
  stock. Immutable selections retain absent inputs as absent.
* Partial totals sum known disjoint components once. The unsourced asset,
  liability and equity rows mean this first slice is **not** a complete balanced
  statement. Do not publish a zero reconciliation residual as proof of accuracy.
* API rows own the hierarchy, metric values, provenance and status. The dashboard
  reuses statement typography and indentation, rounds display values, preserves
  exact CSV amounts and marks estimates. TBD is never a zero-only row.

## Next independent evidence

Pinned Sky debt; non-stable treasury inventory; gross savings-funded asset and
controlled-share eliminations; closing Sky-direct ownership; offchain loan
principal/interest receivables; accrued expense opening balances; opening capital,
retained earnings and own-token equity credits. Extend these source-backed rows
before describing the statement as complete. Do not infer them from the gap.

## Chain closing snapshots (policy v2)

`balance_sheet_sources.py` captures only month-end and completed-day stocks.
It never calls BA Labs or calibrates to benchmark balances. Chain state is the
authority; the benchmark comparison only identified contracts worth checking.
The collector saves finalized boundary headers, raw getter responses, closing
LP ownership and a content hash in `reporting_balance_sheet_snapshots`.
HTTP reads remain RPC-free. V1 immutable selections retain their original layout,
inputs and hash; v2 selections additionally pin the `stocks` publication.

| Gap | Closing source / treatment |
|---|---|
| Sky debt | Vat `ilks(ilk)`: normalized debt × rate / 10^45; no invented netting against Sky-owned pools |
| RLUSD / USDG SparkLend | ALM receipt-token balances, with the configured token scale |
| Savings-funded WETH | Full SparkLend WETH asset at closing onchain ETH/USD; savings claims are separate liabilities |
| Sentora RLUSD | Owned ERC4626 shares converted to underlying RLUSD |
| PAU sUSDC | PAU's ERC4626 claim, separate from idle wallet cash |
| PSM3 | ALM `shares(address)` converted through `convertToAssetValue`; not PSM reserves and not direct ALM sUSDS |
| Arbitrum / XLayer savings | Verified vault `asset()` and gross `totalAssets()`; historical absence requires an empty-code proof |
| Savings idle assets | Add only underlying balances outside the existing measured-cash perimeter |
| AMMs | Actual owned NFTs / LP shares at the pinned closing state, including V4 fee-growth claims |
| Sky-direct pools | Historically designated USDT principal leg belongs to Sky. Other legs and V4 fee claims belong to Spark. PYUSD is not SDE |
| Treasury | Reuse the entity's authenticated closing token inventories; market-quote valuations remain estimates and retain their component source |
| Anchorage | Offchain loan claim remains unavailable until principal repayments and opening principal are independently supported; an escrow wallet balance is not the loan |

Approved stablecoins use the existing USD-par accounting convention. ETH uses
the onchain Chainlink ETH/USD feed, with freshness and round checks. A missing
RPC, unsupported getter or stale oracle produces an unavailable value, never zero.
XLayer uses `XLAYER_RPC` when configured, otherwise OKX's documented public
mainnet endpoint (`https://rpc.xlayer.tech`). Never store private RPC URLs in proof
bundles or logs.

Replay closing stocks without recalculating daily P&L or MSC:

```sh
python -m settle.revenue.balance_sheet_job --from 2026-01-01 --to 2026-11-09 --workers 3
```

Set `SPARK_BALANCE_SHEET_ENABLED=1` on the existing Spark reporting publisher to
refresh closing evidence going forward. Run the reporting publisher only; this
must never trigger an MSC replay. Recheck partial publications and their `errors`.
Opening equity, contributed capital, controlled-share eliminations and payables
remain explicit gaps. Do not force the balance sheet to balance or relabel its
known subtotal as complete equity merely to match another dashboard.
