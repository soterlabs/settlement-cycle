# Allocation funding, PnL and yield

Implementation in progress on `feat/allocation-capital-cost-pnl`.

## Agreed accounting contract (2026-09-20)

Total debt per ilk, the existing global borrowing charge, and settled amounts
remain unchanged. Allocation analytics are downstream of those calculations.
Only borrowed principal carries an allocation funding charge. Appreciation,
cash income, grants and other own funds do not become borrowing when reinvested.

Allocation charges plus an explicit prime financing adjustment reconcile to
the existing BR charge. The bridge to the existing Sky claim remains:

```
allocation charges + prime financing adjustment = existing BR cost
existing BR cost + SDE revenue - spread reimbursement = existing Sky revenue
```

The adjustment includes financing outside traced allocations, capitalized debt
charges, and differences between global deductions and principal attribution.
It is not redistributed across positions to make a check pass.

## Principal ledger

Replay from inception to the exact reporting pins. An opening market value is
never an opening borrowed balance. Positive Vat `frob` darts, scaled by the
execution-block ilk rate, introduce loan proceeds. `grab` is deliberately not
an allocation draw; the existing global debt calculation still includes it.

Each holding carries value and borrowed basis separately. A redemption moves
the weighted-average borrowed fraction of the shares redeemed. Actual cash
proceeds carry the resulting principal into another holding. Appreciation
changes value, not borrowed basis. Realized principal losses cannot fund a new
deposit; their financing remains at prime level. Repayment with own funds
reduces outstanding basis proportionally across the remaining holdings.

Transactions retain both asset legs and are ordered within the day. Daily
balances use the settlement engine's end-of-day convention. Funding costs use
the existing daily blended borrowing rate; they are not rescaled to exhaust
the global borrowing charge.

## Sources and custody

- Raw ALM/beneficial-holder logs, with transaction identities and finalized pins.
- aToken `Mint`, `Burn` and `BalanceTransfer` with their execution indices;
  ordinary `Transfer` sums include rebasing effects and are not used as basis.
- ERC-4626 deposit cash amounts, avoiding rounded one-share price quotations.
- Maple queue requests/processing/refunds, with historical PoolManager and
  WithdrawalManager authentication.
- ERC-7540 subscription/redemption custody, including historical gateways
  authenticated through `share()` and `asset()`.
- CCTP v1 message identity (domain, nonce and messenger), with body validation;
  amount/time proximity is not a bridge-matching rule.

Unmatched receipts/outflows are retained as evidence. They do not create
presumed loan proceeds. Funding uncertainty follows a holding on reinvestment.
Unsupported or unresolved allocations have a null cost, not a fabricated zero.
The feature is not ready for production activation until the required routes
are traced and results reviewed.

## Analytics and development checks

Per allocation: opening/closing/average borrowed principal, funding cost,
prime-side revenue minus funding cost, gross APY and net APY. Annualization uses
the existing time-weighted exposure; zero exposure produces an undefined APY.
Gross APY uses revenue before SDE sharing, including external rewards. Net APY
uses prime-side revenue after funding cost.

The 0–8% gross-yield range is a development investigation aid only. No range
warning, recurring check, or publication gate is added to production.

## Validation so far

August 2026 OBEX, using inception-to-August on-chain principal replay and the
existing August revenue/rate artifact (without rewriting that artifact):

| Metric | USD unless stated |
|---|---:|
| Closing traced venue principal | 384,224,980.60 |
| Venue funding cost | 1,195,814.76 |
| Prime financing adjustment | 52,902.10 |
| Existing BR cost, unchanged | 1,248,716.85 |
| Venue PnL after funding | 435,914.56 |
| Gross APY | 4.8818% |

The replay matches the Maple queue exits, later USDC receipts, and repayments;
it has no unmatched receipts/outflows. Osero's SparkLend position also traces
to its draws; its August gross APY is 1.8158%. A separate $1.078572 USDC receipt
does not create borrowed principal.

Outstanding validation: Spark/Grove history and custody migrations, NFT LP
positions, off-chain principal, bridge versions beyond CCTP v1, and exact daily
idle/SDE attribution. Daily/monthly entrypoint activation and report integration
follow that validation. The current shared compute hook is opt-in.
