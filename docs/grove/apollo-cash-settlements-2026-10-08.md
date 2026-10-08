# Apollo: Ethereum cash precedes Plume share cancellation

The [October 2 Plume spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251002/GrovePlume_20251002.sol)
onboards the Apollo allocation. Two subsequent redemptions pay cash on
Ethereum before the Plume shares are removed:

| Cash date | Cash including test | Plume cancellation date |
|---|---:|---|
| May 11, 2026 | $18,077,675.30 | May 12 |
| August 10, 2026 | $12,263,707.47 | August 10, later that day |

Each group includes a $1 cash test and a one-share cancellation, as well as
the larger legs. A share is worth more than $1: treating the test and large
legs as separate redemptions produces opposite small differences. Together,
actual cash matches the emitted share price times cancelled shares to within
one micro-USDC (the largest numerical difference is $0.000000043).

The two main cash transfers and May test originate from `0xcd531…ca7b`; the
August $1 test originates from `0xa9d1…3e43`. This is an explicitly reviewed
off-chain settlement association, supported by cash totals and corresponding
share cancellations, not an authenticated bridge-message link. It does not
classify unrelated payments from those wallets.

All canonical cash, token, share cancellation and price events are committed
in `tests/fixtures/grove_apollo_cash_settlement_events.json`. The fixture also
reconstructs actual held shares from inception. No other Apollo ownership
change intervenes between each group's cash receipts and cancellations.

## Basis accounting

Each actual cash receipt releases the proportional borrowed basis immediately.
The cash-settlement price and verified held/redeemed share quantities determine
that proportion; no later oracle price is applied at the receipt. Earnings
remain unborrowed. The later token cancellation marks the remaining shares
without releasing basis a second time. This prevents returned capital from
being counted in both Apollo and Ethereum cash.

All cash and cancellation legs must be present in pinned inputs before this
historical adapter applies. A cutoff before cancellation stays unresolved;
the adapter neither fetches future events nor guesses the missing association.
Changed amounts, holdings, blocks or intervening ownership changes fail.

## August replay

Unmatched receipts fall **40 → 36**, and outflows **260 → 256**. The eligible
subtotal is still $1,233.51; this is progress in tracing, not full reconciliation.

The tighter GROVE-A interval is now **$9,940.19–$11,693.82**, exposing **$90.92**
of its $11,784.74 control outside currently mapped allocations. The difference
is concentrated on August 19. The next investigation is secondary-ALM cash:
the PAU's cash movements are replayed but its raw cash accounts do not yet have
allocation rows. Do not hide this mismatch by assigning a residual.

Evidence: `reconciliation/grove_apollo_cash_settlements_2026_08.json`. The
full focused suite had 307 passing tests and one overly strict canonical
price comparison; allowing one micro-USDC for that observed rounding then
passed all 10 Apollo tests. The preceding broad suite passed 1,649 tests
(two environment-dependent skips). No settlement, API revenue or global CoF
control changed.
